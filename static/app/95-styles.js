// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// пресеты стиля, профили спикеров, пикеры, музыка, движки ASR
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= styles (ported) =================
let STYLES={},CURSTYLE=null,STYLESAVED='base',FONTS=[];
// Режим правки шаблона: STYLE_EDITING — имя открытого на карандаш шаблона,
// STYLE_EDIT_ORIG — его снимок до правок (для «несохранённые?»), STYLE_TOUCHED — флаг
// «поля трогали» для кастома. Селектор в этом режиме показывает имя + « — правится».
let STYLE_EDITING=null,STYLE_EDIT_ORIG=null,STYLE_TOUCHED=false;
// ---- спикеры: кто в кадре -> пороги нарезки + папка + стиль AE ----
// Пороги в gigaam_cut калибровались на спикере A; у другого спикера другая студия
// и другой голос, и те же цифры режут не там (замеры — в speakers.py). Селектор
// один раз ставит всё, что зависит от спикера, чтобы это не выбиралось руками
// каждый прогон (и не забывалось — из-за чего клипы уезжали в чужую папку).
let SPEAKERS={},SPKSAVED='',SPKDEF={},SPKLAB=[],SPKEDIT='',SPKFORMATS={},SPKFORMAT_ORDER=[],SPKDEF_FORMAT='9:16';
// Глобальная папка для .jsx (клипы БЕЗ тега спикера) — отдельно от того, что поле
// показывает у клипа с тегом. Тег определяет папку клипа (профиль спикера), поле —
// вид на неё; глобальное значение хранится здесь, чтобы показ папки спикера не
// затирал папку для клипов без тега («клип без спикера — как сегодня»).
let AEGLOBAL='';
// Папка вывода безголового рендера (шаг 3). Та же механика, что у
// AEGLOBAL: у клипа со спикером — из его профиля (renderdir), у клипа без тега —
// глобальная; дефолт — папка exp рядом с репозиторием (подставляет сервер).
let AERENDER='';
async function loadSpeakers(){let d;
  try{d=await (await fetch('/api/speakers')).json();}
  catch(e){uiLog(t('loadSpeakers(запрос): ')+e);return;}
  if(!d.ok){uiLog(t('loadSpeakers(ответ): ')+(d.error||t('ответ без ok')));return;}
  SPEAKERS=d.speakers||{};SPKDEF=d.defaults||{};SPKLAB=d.labels||[];
  // Форматы кадра — из core/frame.py (роут отдаёт их вместе с профилями): список
  // один на весь проект, второй копии в интерфейсе нет. Порядок пунктов приезжает
  // отдельным полем format_order: у словаря JSON порядка нет, ключи Flask сортирует
  // по алфавиту — по Object.keys список шёл бы 16:9, 1:1, 4:5, 9:16.
  SPKFORMATS=d.formats||{};SPKFORMAT_ORDER=d.format_order||[];
  SPKDEF_FORMAT=d.default_format||'9:16';
  spkFormatFill();
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
  // Папка вывода рендера — так же из профиля, см. renderRenderDirField.
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
// Папка для .jsx КЛИПА: тег спикера определяет её у клипа, и каждый
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
// Сохранение на каждый ввод: поля папок были голыми инпутами,
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
// глобального спикера или ручная (пункт 2). Раньше человек видел путь
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
// Папки камер из профиля: camdirs[k] подставляется при выборе спикера.
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
// Поля LUT в модалке спикера — тоже по строке на камеру (nCams()), как папки камер:
// у каждой камеры своя таблица .cube, общей быть не может (разные объективы и свет).
function spkLutFill(lut){
  const host=$('spk_luts');if(!host)return;host.innerHTML='';
  const m=lut||{};
  for(let k=0;k<nCams();k++){
    const row=document.createElement('div');row.className='setrow';
    row.innerHTML='<label>'+esc(t('Камера {n}',{n:k+1}))+'</label>'
      +'<input id="spk_lut'+k+'" placeholder="'+esc(t('пусто — LUT не накладывается'))+'">'
      +'<button class="sm" onclick="pickcube(\'spk_lut'+k+'\')">'+esc(t('Выбрать…'))+'</button>'
      +'<button class="sm" onclick="lutClear('+k+')">'+esc(t('Убрать'))+'</button>';
    host.appendChild(row);$('spk_lut'+k).value=(m[String(k+1)]||'');}}
// «Убрать» гасит только поле: в профиль правка уезжает по «Сохранить», как у
// остальных полей окна (иначе кнопка сохраняла бы профиль за спиной у юзера).
function lutClear(k){const el=$('spk_lut'+k);if(el){el.value='';el.focus();}}
// Смена спикера подставляет ЕГО папки и стиль.
async function onSpeakerChange(){const p=SPEAKERS[val('speaker')];
  if(p){
    await spkDir('ai_outdir','outdir',t('Папка результата задана вручную. Поставить папку спикера?\n'));
    // .jsx уезжают в проект AE того же человека — папка у них своя и такая же личная,
    // как папка нарезок. Раньше её меняли руками на шаге сборки и забывали.
    await spkDir('aeoutdir','jsxdir',t('Папка для .jsx задана вручную. Поставить папку спикера?\n'));
    // Папки камер — те же личные данные: свой материал, свои исходники.
    // Механика та же, что у spkDir, только массив: camdirs[k]. Пусто — автоподбор.
    for(let k=0;k<nCams();k++)await camDirApply(k,t('Папка камеры {n} задана вручную. Поставить папку спикера?\n',{n:k+1}));
    const st=$('style');
    // Стиль спикера — ЗНАЧЕНИЕ ПО УМОЛЧАНИЮ: ставим его здесь, а на шаге сборки юзер
    // меняет стиль как хочет, в профиль это не возвращается. Пропавший шаблон (стиль
    // переименовали или удалили) раньше молча игнорировался — и клип собирался чужим.
    // Клип с тегом живёт СВОИМ стилем: смена глобального спикера на шаге 1
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
// Формат кадра ролика: список из core/frame.py (приезжает с /api/speakers). Первым
// пунктом — формат по умолчанию: у профилей, заведённых до появления поля, его нет,
// и такой ролик обязан собираться как раньше (9:16).
// Порядок пунктов задаёт format_order — ключи core/frame.py по порядку (9:16, 1:1,
// 4:5, 16:9). У словаря `formats` порядка нет (Flask сортирует ключи JSON), и по
// Object.keys формат по умолчанию стоял бы последним. Поля нет (старый сервер) —
// прежнее поведение: порядок по ключам словаря.
function spkFormatFill(){
  const sel=$('spk_format');if(!sel)return;
  const cur=sel.value;
  sel.innerHTML='';
  const list=(SPKFORMAT_ORDER&&SPKFORMAT_ORDER.length)
    ?SPKFORMAT_ORDER.slice():Object.keys(SPKFORMATS);
  if(list.indexOf(SPKDEF_FORMAT)<0)list.unshift(SPKDEF_FORMAT);
  list.forEach(f=>{const o=document.createElement('option');
    o.value=f;o.textContent=(f===SPKDEF_FORMAT)?(f+' — '+t('как раньше')):f;sel.appendChild(o);});
  sel.value=(cur&&list.indexOf(cur)>=0)?cur:SPKDEF_FORMAT;}
function openSpeaker(key){
  SPKEDIT=SPEAKERS[key]?key:'';
  const p=SPKEDIT?SPEAKERS[SPKEDIT]:{};
  $('spkTitle').textContent=SPKEDIT?t('Спикер: ')+(p.label||SPKEDIT):t('Новый спикер');
  $('spk_label').value=SPKEDIT?(p.label||SPKEDIT):'';
  $('spk_outdir').value=p.outdir||'';
  $('spk_jsxdir').value=p.jsxdir||'';
  $('spk_renderdir').value=p.renderdir||'';
  spkCamDirFill(p.camdirs||[]);
  spkLutFill(p.lut||{});
  $('spk_hint').value=p.hint||'';
  $('spk_note').value=p.note||'';
  $('spk_bpcut').value=(p.breath_p_cut!=null?p.breath_p_cut:'');
  $('spk_bpmark').value=(p.breath_p_mark!=null?p.breath_p_mark:'');
  const ins=p.inserts||{};
  $('spk_ins_photo').value=(ins.photo!=null?ins.photo:'');
  $('spk_ins_video').value=(ins.video!=null?ins.video:'');
  const ips=p.image_prompts||{};
  $('spk_extra_a').value=(ips.a&&ips.a.extra)||'';
  $('spk_pos_a').value=(ips.a&&ips.a.pos==='prefix')?'prefix':'suffix';
  $('spk_extra_b').value=(ips.b&&ips.b.extra)||'';
  $('spk_pos_b').value=(ips.b&&ips.b.pos==='prefix')?'prefix':'suffix';
  // pa/pb — приписки вставок с галкой «на подложке»: тот же формат, что a/b,
  // и так же живут в image_prompts профиля
  $('spk_extra_pa').value=(ips.pa&&ips.pa.extra)||'';
  $('spk_pos_pa').value=(ips.pa&&ips.pa.pos==='prefix')?'prefix':'suffix';
  $('spk_extra_pb').value=(ips.pb&&ips.pb.extra)||'';
  $('spk_pos_pb').value=(ips.pb&&ips.pb.pos==='prefix')?'prefix':'suffix';
  const vps=p.video_prompts||{};
  $('spk_video_extra_a').value=(vps.a&&vps.a.extra)||'';
  $('spk_video_pos_a').value=(vps.a&&vps.a.pos==='prefix')?'prefix':'suffix';
  $('spk_video_extra_b').value=(vps.b&&vps.b.extra)||'';
  $('spk_video_pos_b').value=(vps.b&&vps.b.pos==='prefix')?'prefix':'suffix';
  const ss=$('spk_style');ss.innerHTML='<option value="">'+t('не задан')+'</option>';
  Object.keys(STYLES).forEach(k=>{const o=document.createElement('option');
    o.value=k;o.textContent=t(STYLES[k].label||k);ss.appendChild(o);});
  ss.value=(p.style&&STYLES[p.style])?p.style:'';
  spkFormatFill();
  const fs=$('spk_format');if(fs)fs.value=(p.format&&SPKFORMATS[p.format])?p.format:SPKDEF_FORMAT;
  spkGrid(p.cut||{});
  // Голос — только сводкой: ручек здесь нет и не будет, крутят их в панели «Голос»
  // превью нарезки, на звуке клипа (та же функция разметки, см. voiceFxRender).
  voiceFxRender($('spkVoice'),p.voice_fx,{mode:'summary'});
  $('spk_del').style.display=SPKEDIT?'':'none';
  openModal('mbSpeaker');}
// ---- пересчёт стиля под кадр формата ----
// Стиль задуман в кадре 1080×1920 и на все форматы один. При сборке его числа
// пересчитывает Python (core/style_geometry.py), а здесь — то же правило для
// ЖИВОГО превью: панель правит стиль в базовых единицах, а превью рисует по
// кадру ролика, и без пересчёта в квадрате интро стояло бы за краем.
// Таблица «поле -> вид» приезжает полем geo в схеме панели (/api/style_schema):
// второй её копии здесь нет — только формула, и та одна (stScaleFactor).
const ST_BASE_W=1080,ST_BASE_H=1920;
function stGeoTable(){
  const g=STSCHEMA&&STSCHEMA.geo;
  return (g&&typeof g==='object')?g:null;}
// Множитель вида поля в кадре w×h. Нет таблицы (схема не приехала) — 1: превью
// работает как раньше, вертикаль остаётся вертикалью.
function stScaleFactor(kind,w,h){
  const g=stGeoTable();if(!g||!kind)return 1;
  const W=+w||0,H=+h||0;if(!(W>0)||!(H>0))return 1;
  if(kind==='x')return W/ST_BASE_W;
  if(kind==='y')return H/ST_BASE_H;
  if(kind==='size')return Math.min(W,H)/ST_BASE_W;
  return 1;}
function stScaleGeo(key,w,h){const g=stGeoTable();return (g&&g[key])?stScaleFactor(g[key],w,h):1;}
// Множитель вида (x/y/size) без конкретного поля: какое поле представляет вид,
// говорит бэкенд (geo_axis схемы) — своей таблицы «вид -> поле» на фронте нет.
function stAxisKey(kind){const a=STSCHEMA&&STSCHEMA.geo_axis;return (a&&a[kind])||null;}
function stScaleKind(kind,w,h){const key=stAxisKey(kind);return key?stScaleGeo(key,w,h):1;}
function stScaleRound(v){
  if(typeof v!=='number'||!isFinite(v))return v;
  return Math.round(v*100)/100;}
// Стиль, пересчитанный под кадр w×h: вход не меняется, ключи вне таблицы — как есть.
function stScaleStyle(s,w,h){
  if(!s)return s;
  const out={};for(const k in s)out[k]=s[k];
  const g=stGeoTable();if(!g)return out;
  for(const k in g){
    if(typeof out[k]==='number'&&isFinite(out[k]))out[k]=stScaleRound(out[k]*stScaleFactor(g[k],w,h));}
  return out;}
// Обратный пересчёт для драга: экранный сдвиг в px КАДРА -> базовые единицы
// стиля. Без него перетащил на квадрате — а в вертикали уехало.
function stUnscaleGeo(v,kind,w,h){
  const k=stScaleFactor(kind,w,h);
  return k?stScaleRound(v/k):v;}

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

// ---- голос: ОДИН компонент на два места (панель превью и сводка профиля) ----
// Настройки живут в профиле спикера (поле voice_fx) и запекаются в WAV ДО After
// Effects: в проект уедет уже обработанный голос камеры 1, по нему же будет резать
// нарезка. Но КРУТЯТ их ровно в одном месте — в панели «Голос» превью нарезки, на
// звуке клипа: там слышно, что выходит (в профиле крутить нечего, слушать нечего).
// Поэтому и разметка, и чтение значений — одна пара функций (voiceFxRender и
// voiceFxRead): второй копии блока ни в редакторе профиля, ни где-либо ещё нет.
// Состояние панели живёт В САМОЙ РАЗМЕТКЕ (порядок строк цепочки = порядок
// обработки), а не в отдельной переменной: два места об одном и том же разъезжаются.
//
// ВЫКЛЮЧАТЕЛЬ ОДИН — «ИИ-шумодав» (и цепочка плагинов под ним). Галок «для нарезки»
// и «в итоговый трек» больше нет: включено — работает ВЕЗДЕ и ВЕСЬ (нарезка, итоговый
// трек AE/DRP/XML, черновой рендер, все превью). Правило одно и на сервере
// (core/voicefx.py:voice_fx_on) — здесь его зеркало для разметки (voiceFxOn).
const VFX_DB_DEFAULT=40;
let VSTLIST=[];              // найденные VST3: список один на страницу (пункт «Добавить плагин»)
let VOICEFXSPK='';           // ключ профиля спикера клипа, открытого в превью (см. pvVoicePanel)
// Сила RoFormer — доля обработанного в смеси с исходником, %: 100 = только
// обработанный (штатный режим, владелец выбирал движок по этому звуку). У
// deep-filter сила другая — предел подавления в дБ (VFX_DB_DEFAULT).
const VFX_ENGINE_DEFAULT='roformer';
const VFX_MIX_DEFAULT=100;
// Состояние окружения RoFormer (одно на страницу): что стоит, что качать и как
// идёт установка. Спрашивается при открытии панели и опрашивается во время
// установки (voiceFxSepFill / voiceFxSepWatch).
let VFXSEP=null;
let VFXSEP_TIMER=0;
const VFXSEP_POLL=1500;      // опрос хода установки, мс: шаги идут минутами
// Движок шумодава из настроек профиля. Явное значение — как записано; поля нет —
// RoFormer: он движок по умолчанию (DeepFilterNet остаётся в списке, им чистят
// паузы, но шорох одежды поверх речи он не берёт).
function vfxEngine(f){
  const dn=(f&&typeof f.denoise==='object')?f.denoise:{};
  if(dn.engine==='roformer'||dn.engine==='roformer_aggr')return dn.engine;
  if(dn.engine==='deepfilter')return 'deepfilter';
  return VFX_ENGINE_DEFAULT;}
function vfxEngName(eng){
  if(eng==='roformer')return t('RoFormer (мягкий)');
  if(eng==='roformer_aggr')return t('RoFormer (жёсткий)');
  return 'DeepFilterNet';}
// Подписи ползунка меняются по движку: у deep-filter это предел подавления в дБ,
// у RoFormer — доля обработанного в смеси, %.
function vfxStrengthLabel(eng){return eng==='deepfilter'?t('Подавление, дБ'):t('Доля обработанного, %');}
function vfxStrengthHint(eng){
  if(eng==='deepfilter')return t('Предел подавления в дБ: 0 — без обработки, 100 — глушит вместе с шумом и голосом. Начни с 30-40.');
  return t('Доля обработанного голоса в смеси с исходником, %: 100 — только обработанный, 50 — полусумма с исходником, 0 — без обработки. Ручку слышно сразу, пока идёт прослушивание.');}
// Сила ДРУГОГО движка лежит в скрытом поле: ползунок один, а значений два, и без
// запаса переключение движка туда-обратно молча теряло бы настройку.
function vfxAltDefault(eng){return eng==='deepfilter'?VFX_MIX_DEFAULT:VFX_DB_DEFAULT;}
// Текущий движок из РАЗМЕТКИ (data-engine ставит и разметка, и dnEngineSync), а не
// из <select>.value: состояние панели живёт в разметке, и читающий его код не
// должен зависеть от того, как выпадающий список ведёт себя в конкретном DOM.
function vfxEngineSel(host){
  const sel=vfxEl(host,'dn_engine');if(!sel)return VFX_ENGINE_DEFAULT;
  return sel.dataset.engine||sel.value||VFX_ENGINE_DEFAULT;}
// Панель, к которой относится элемент разметки: строки цепочки, галки и кнопки зовут
// обработчики с `this`, а работают всегда в пределах своей панели.
function voiceFxHost(el){return (el&&el.closest)?el.closest('[data-vfxroot]'):null;}
function vfxEl(host,k){return host?host.querySelector('[data-vfx="'+k+'"]'):null;}
function vstTitle(p){return (p&&(p.title||p.name))||((p&&p.path||'').replace(/^.*[\\\/]/,''));}
// Обработка включена: шумодав или хоть один плагин с галкой. Это ЗЕРКАЛО серверного
// правила (core/voicefx.py:voice_fx_on) — одно условие на значения профиля
// (voiceFxLive) и на разметку (voiceFxOn); второй копии быть не должно.
function voiceFxLive(fx){
  const f=(fx&&typeof fx==='object')?fx:{},dn=(f.denoise&&typeof f.denoise==='object')?f.denoise:{};
  return !!(dn.on||(Array.isArray(f.vst)&&f.vst.some(v=>v&&v.on!==false)));}
function voiceFxOn(host){
  const dn=vfxEl(host,'dn_on');if(dn&&dn.checked)return true;
  return Array.from(host.querySelectorAll('[data-vfx="vst_on"]')).some(c=>c.checked);}
// ЖИВОЙ ХОСТ клипа — этим живёт звук превью. Пока открыто превью и в цепочке есть
// включённые плагины, голос клипа играет ПРОЦЕСС ХОСТА (трек через цепочку
// вживую, вровень с картинкой), а окно плагина — лишь его панель: открыл/закрыл,
// звук не рвётся. Состояние живёт на странице (VOICEFXLIVE), потому что о нём
// знают двое: панель «Голос» поднимает хост и открывает окна, а плеер спрашивает
// перед каждым кадром. В профиле этого нет: хост живёт только здесь и только сейчас.
//
// Три вопроса — три ответа, и все нужны:
//   * `voiceFxHostOn` — хост поднят (процесс жив), но звучит ли он, ещё неизвестно;
//   * `voiceFxLiveOn` — хост РЕАЛЬНО звучит: дорожка шумодава досчитана (только тогда
//     плеер глушит свой голос — раньше глушение означало бы тишину вместо голоса).
//     `track_ready` явно `false` — «дорожки ещё нет»; страница ставит его всегда
//     булевым, а незаданный считается готовым (так стенды, ставящие только sid/running);
//     `audio_error` — «звук хоста не идёт» (устройство не приняло частоту, поток
//     отвалился): тогда глушить свой голос НЕЛЬЗЯ, иначе человек слышит тишину;
//   * `voiceFxWindowOn` — окно плагина открыто прямо сейчас (пока оно открыто, свои
//     ручки панели не записываем: состояние вот-вот отдаст окно).
let VOICEFXLIVE=null;
function voiceFxHostOn(){return !!(VOICEFXLIVE&&VOICEFXLIVE.running);}
function voiceFxLiveOn(){return !!(VOICEFXLIVE&&VOICEFXLIVE.running&&!VOICEFXLIVE.audio_error&&VOICEFXLIVE.track_ready!==false);}
function voiceFxWindowOn(){return !!(VOICEFXLIVE&&VOICEFXLIVE.running&&VOICEFXLIVE.window);}
// Включённые плагины цепочки: одно решение на «нужен ли живой хост» и на подпись
// панели — второй копии правила быть не должно.
function voiceFxHasVst(fx){
  const f=(fx&&typeof fx==='object')?fx:{};
  return !!(Array.isArray(f.vst)&&f.vst.some(v=>v&&v.path&&v.on!==false));}
// Строка-сводка значений профиля: то, что видно там, где крутить нечего (редактор
// профиля). Словами, а не галками: у обработки голоса одно решение — включена она
// или нет, и включённая работает и в нарезке, и в итоговом треке, и в превью.
function voiceFxSummary(fx){
  const f=(fx&&typeof fx==='object')?fx:{};
  const dn=(f.denoise&&typeof f.denoise==='object')?f.denoise:{};
  const vst=Array.isArray(f.vst)?f.vst.filter(v=>v&&v.path):[];
  const on=vst.filter(v=>v.on!==false).length;
  if(!voiceFxLive(f))return t('Обработка голоса не настроена');
  const parts=[];
  const eng=vfxEngine(f);
  const dbs=(dn.atten_db!=null?dn.atten_db:VFX_DB_DEFAULT);
  const pct=(dn.mix!=null?dn.mix:VFX_MIX_DEFAULT);
  if(!dn.on)parts.push(t('Шумодав выключен'));
  else if(eng==='deepfilter')parts.push(t('Шумодав {db} дБ',{db:dbs}));
  else parts.push(t('Шумодав {engine} {pct} %',{engine:vfxEngName(eng),pct:pct}));
  parts.push(vst.length?t('плагинов {n}',{n:vst.length})+(on<vst.length?t(' (включено {on})',{on:on}):''):t('плагинов нет'));
  parts.push(t('работает везде: нарезка, итоговый трек, превью'));
  return parts.join(' · ');}
function vfxDnSummaryText(eng, str){
  if(eng==='deepfilter')return 'DeepFilterNet · '+str+' дБ';
  return vfxEngName(eng)+' · '+t('доля')+' '+str+' %';}
function vfxDnToggle(btn){
  const host=voiceFxHost(btn);if(!host)return;
  const det=host.querySelector('[data-vfx="dn_details"]');if(!det)return;
  const open=(det.style.display!=='none');
  det.style.display=open?'none':'block';
  btn.textContent=open?t('▾ настройки'):t('▴ скрыть');}
function vfxDnUpdateSummary(host){
  if(!host)return;
  const sum=host.querySelector('[data-vfx="dn_summary"]');
  if(!sum)return;
  const eng=vfxEngineSel(host);
  const str=voiceFxStrength(host);
  sum.textContent=vfxDnSummaryText(eng,str);}
// Разметка блока. mode: panel — панель настроек (превью нарезки), summary — сводка
// (редактор профиля), off — крутить нечего (у клипа нет спикера или профиль пропал).
function voiceFxRender(host,fx,opts){
  if(!host)return;
  const o=opts||{},f=(fx&&typeof fx==='object')?fx:{};
  host.setAttribute('data-vfxroot','1');
  host.dataset.vfxmode=o.mode||'panel';
  if(o.mode==='summary'){
    host.innerHTML='<div class="hint">'+esc(voiceFxSummary(f))+'</div>'
      +'<div class="hint" style="margin-top:6px;font-size:11.5px">'
      +t('Настраивается в превью нарезки — на звуке клипа: там слышно, что выходит.')+'</div>';
    return;}
  if(o.mode==='off'){
    host.innerHTML='<div class="hint">'+esc(o.note||t('У клипа нет спикера — обработка голоса настраивается в профиле спикера.'))
      +' <span class="i" data-t="'+esc(t('Обработка голоса — настройка ПРОФИЛЯ спикера: у клипа без тега профиля нет, поэтому и панели нет. Поставь тег спикера на клип или открой клип с тегом.'))+'">!</span></div>';
    tipArm(host);
    return;}
  const dn=(f.denoise&&typeof f.denoise==='object')?f.denoise:{};
  const eng=vfxEngine(f);
  const att=(dn.atten_db!=null?dn.atten_db:VFX_DB_DEFAULT);
  const mix=(dn.mix!=null?dn.mix:VFX_MIX_DEFAULT);
  // Ползунок показывает силу ТЕКУЩЕГО движка, скрытое поле — силу другого (см. dnEngineSync).
  const str=(eng==='deepfilter')?att:mix;
  const alt=(eng==='deepfilter')?mix:att;
  const vst=(Array.isArray(f.vst)?f.vst:[]).filter(v=>v&&v.path);
  const curDb=(typeof CURSTYLE!=='undefined'&&CURSTYLE&&CURSTYLE.voice_db!=null)?CURSTYLE.voice_db:0;
  const vdbVal=Math.round(curDb*2)/2;
  const vdbTxt=(vdbVal>=0?'+':'')+vdbVal.toFixed(1)+' dB';
  // ВЫКЛЮЧАТЕЛЬ ОДИН. Галок «Для нарезки» и «В итоговый трек» здесь больше нет:
  // включённая обработка работает везде и весь, и второго решения у неё нет.
  // Списки (движок, плагины, «куда играть») — по ширине содержимого, а не на всю
  // панель: растянутый на полэкрана выпадающий список читается хуже строки под ним.
  let h='<div class="setrow" style="margin-bottom:10px">'
    +'<label data-t="'+esc(t('Громкость голоса спикера'))+'">'+t('Громкость')+'</label>'
    +'<span style="display:flex;gap:12px;align-items:center;flex:1;min-width:0">'
    +'<input type="range" id="pvvoicedb" data-vfx="voice_db" min="-60" max="12" step="0.5" value="'+vdbVal+'" style="flex:1;min-width:0" oninput="setStyleDb(\'voice\',this.value)">'
    +'<span id="pvvoicedbv" style="width:62px;text-align:right;font-variant-numeric:tabular-nums;font-size:12px">'+vdbTxt+'</span>'
    +'</span></div>'
    +'<div class="sethdr">'+t('Цепочка обработки')+' <span class="i" data-t="'
    +esc(t('Цепочка обработки голоса: шумодав и плагины идут сверху вниз, в том порядке, в каком стоят в списке.'))+'">!</span></div>'
    +'<div class="setrow" style="align-items:center;justify-content:space-between;margin-bottom:4px">'
    +'<label class="chk" style="flex:0 0 auto" data-t="'
    +esc(t('ИИ-шумодав до VST-плагинов: убирает шум комнаты и улицы. Движок — в настройках ниже; выключено — голос идёт как есть, плагины продолжают работать.'))+'">'
    +'<input type="checkbox" data-vfx="dn_on"'+(dn.on?' checked':'')+' onchange="vstFxDn(this)"> '+t('Шумодав')+'</label>'
    +'<span data-vfx="dn_summary" style="font-size:12px;color:var(--mut);margin-left:8px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(vfxDnSummaryText(eng,str))+'</span>'
    +'<button type="button" class="sm link" data-vfx="dn_toggle" onclick="vfxDnToggle(this)" style="background:none;border:none;color:var(--tx);cursor:pointer;padding:2px 6px;font-size:12px;flex:0 0 auto">'+t('▾ настройки')+'</button>'
    +'</div>'
    +'<div data-vfx="dn_details" style="display:none;padding:6px 0 6px 12px;border-left:2px solid var(--bd);margin:2px 0 8px 6px">'
    +'<div class="setrow"><label data-t="'+esc(t('Движок ИИ-шумодава. RoFormer (мягкий и жёсткий) — mel-band модели в своём окружении: берут шорох одежды поверх речи, ставятся отдельно, кнопкой рядом. DeepFilterNet — прежний движок: чистит паузы, шорох одежды на речи не берёт. Профиль без поля движка работает на RoFormer (мягкий).'))+'">'
    +t('Движок')+'</label>'
    +'<select data-vfx="dn_engine" data-engine="'+eng+'" style="width:auto;max-width:320px" onchange="dnEngineSync(this)">'
    +'<option value="deepfilter"'+(eng==='deepfilter'?' selected':'')+'>DeepFilterNet</option>'
    +'<option value="roformer"'+(eng==='roformer'?' selected':'')+'>'+t('RoFormer (мягкий)')+'</option>'
    +'<option value="roformer_aggr"'+(eng==='roformer_aggr'?' selected':'')+'>'+t('RoFormer (жёсткий)')+'</option>'
    +'</select>'
    +'<button class="sm" data-vfx="sep_install" style="display:none" onclick="voiceFxSepInstall(this)" data-t="'
    +esc(t('Ставит RoFormer в своё окружение (~/.reelsi/voice_sep): системный Python и его пакеты не трогаются. Качаются окружение audio-separator и две модели — ход виден в общей форме прогресса.'))+'">'
    +t('Скачать RoFormer')+'</button>'
    +'<span class="muted" data-vfx="sep_state" style="font-size:11.5px"></span></div>'
    +'<div class="setrow"><label data-vfx="dn_label" data-t="'+esc(vfxStrengthHint(eng))+'">'
    +esc(vfxStrengthLabel(eng))+'</label><span style="display:flex;gap:12px;align-items:center;flex:1;min-width:0">'
    +'<input type="range" data-vfx="dn_atten" min="0" max="100" step="1" value="'+str+'" style="flex:1;min-width:0" oninput="dnAttenSync(this)">'
    +'<input type="number" data-vfx="dn_atten_num" min="0" max="100" step="1" value="'+str+'" style="width:58px" oninput="dnAttenSync(this)">'
    +'<input type="hidden" data-vfx="dn_alt" value="'+alt+'">'
    +'</span></div>'
    +'</div>'
    +'<div data-vfx="vst"></div>'
    +'<div class="setrow" style="margin-top:6px"><select data-vfx="pick" style="width:auto;max-width:320px"></select>'
    +'<button class="sm" onclick="vstFxList(this,true)" data-t="'
    +esc(t('Перечитать список плагинов: досканируются только новые и обновлённые — уже прочитанные берутся из кеша. Плагин грузит отдельный процесс, в сервере он не открывается.'))+'">'
    +t('Обновить список')+'</button>'
    +'<button class="sm" onclick="vstFxAdd(this)">'+t('+ Добавить плагин')+'</button></div>'
    +'<div class="setrow" style="margin-top:8px"><label data-t="'
    +esc(t('Устройство вывода для живого прослушивания в окне плагина. «По умолчанию» — системное. Настройка этой машины, а не спикера: запоминается в браузере.'))+'">'
    +t('Куда играть')+'</label><select data-vfx="device" style="width:auto;max-width:320px"></select></div>'
    +'<div class="row" style="gap:12px;align-items:center;margin-top:10px;flex-wrap:wrap">'
    +'<span class="muted" data-vfx="autosave" style="font-size:11.5px">'
    +t('Сохраняется само: закрыл окно плагина или отпустил ручку — настройки уже у спикера.')+'</span>'
    +'<span class="grow"></span></div>'
    +'<div class="hint" data-vfx="status" style="margin-top:6px;font-size:11.5px"></div>';
  host.innerHTML=h;
  const box=vfxEl(host,'vst');
  if(box)vst.forEach(p=>box.appendChild(vstRow(p)));
  vstFxNote(host);
  fxDeviceFill(host);
  tipArm(host);}
// Значения панели — то, что уедет в data.voice_fx профиля спикера. Ничего не включено и
// список пуст → null: у профиля, который не трогали, поля voice_fx не появляется (как у
// inserts и lut). Сводка панелью не является — читать из неё нечего.
//
// Галочек назначения в значениях НЕТ: обработка включена или выключена, и это одно
// решение (denoise.on или включённый плагин). Сервер выводит из него и нарезку, и
// итоговый трек, и превью (core/voicefx.py:voice_fx_on) — второй копии решения в
// профиле не хранится, и старые `cut`/`final=false` обработку не выключают.
function voiceFxRead(host){
  if(!host||host.dataset.vfxmode!=='panel')return null;
  const on=!!(vfxEl(host,'dn_on')||{}).checked;
  const vst=Array.from(host.querySelectorAll('[data-vst]')).filter(r=>r.dataset.path).map(r=>({
    path:r.dataset.path,name:r.dataset.name||'',state:r.dataset.state||'',
    on:!!(r.querySelector('[data-vfx="vst_on"]')||{}).checked}));
  if(!on&&!vst.length)return null;
  const eng=vfxEngineSel(host);
  const strength=voiceFxStrength(host);
  const other=voiceFxOther(host,eng);
  return {denoise:{on:on,engine:eng,
      atten_db:(eng==='deepfilter'?strength:other),
      mix:(eng==='deepfilter'?other:strength)},
    vst:vst};}
// Число ползунка силы (0..100; мусор — 0: «силы нет» переживается, а NaN уехал бы
// в профиль и зажался бы там дефолтом уже без объяснения откуда).
function voiceFxStrength(host){
  const v=parseInt((vfxEl(host,'dn_atten_num')||{}).value,10);
  return isNaN(v)?0:Math.max(0,Math.min(100,v));}
// Сила ДРУГОГО движка — из скрытого поля (панель без него — дефолт этого движка).
function voiceFxOther(host,eng){
  const v=parseInt((vfxEl(host,'dn_alt')||{}).value,10);
  return isNaN(v)?vfxAltDefault(eng):Math.max(0,Math.min(100,v));}
// Строка состояния панели: чем занят сервер (считает окно, печёт голос) и что не
// вышло. Одна на панель — и для обработки, и для прослушивания.
function voiceFxStatus(host,text){
  const el=vfxEl(host||$('pvvoice'),'status');if(el)el.textContent=text||'';}
// Ползунок и число — одно значение: без синхронизации они разъезжаются молча
// (число показывает одно, а в профиль уедет то, что осталось в ползунке). Плюс
// «пересчитать окно»: силу шумодава слышно сразу, пока идёт прослушивание.
function dnAttenSync(el){
  const host=voiceFxHost(el);if(!host)return;
  const r=vfxEl(host,'dn_atten'),n=vfxEl(host,'dn_atten_num');if(!r||!n)return;
  let v=parseInt(el===n?n.value:r.value,10);
  if(isNaN(v)||v<0||v>100)v=vfxEngineSel(host)==='deepfilter'?VFX_DB_DEFAULT:VFX_MIX_DEFAULT;
  r.value=v;n.value=v;
  vfxDnUpdateSummary(host);
  pvVoiceTune();}
// Смена движка шумодава. Ползунок один, а сила у движков своя (дБ против %):
// значение прошлого движка уезжает в скрытое поле, из него же берётся значение
// нового — иначе переключение туда-обратно молча теряло бы настройку. Плюс
// подписи ползунка, кнопка установки RoFormer и пересчёт окна прослушивания.
function dnEngineSync(sel){
  const host=voiceFxHost(sel);if(!host)return;
  const was=sel.dataset.engine||VFX_ENGINE_DEFAULT,now=sel.value||VFX_ENGINE_DEFAULT;
  const alt=vfxEl(host,'dn_alt');
  const strength=voiceFxStrength(host);
  const other=voiceFxOther(host,was);
  const r=vfxEl(host,'dn_atten'),n=vfxEl(host,'dn_atten_num');
  if(r)r.value=other;if(n)n.value=other;
  if(alt)alt.value=strength;
  sel.dataset.engine=now;
  const lab=vfxEl(host,'dn_label');
  if(lab){lab.textContent=vfxStrengthLabel(now);lab.setAttribute('data-t',vfxStrengthHint(now));}
  if(typeof voiceFxStatus==='function')voiceFxStatus(host,'');
  if(typeof vtNote==='function'&&typeof ED!=='undefined')vtNote(ED,'');
  voiceFxSepFill(host);                 // окружения для нового движка может не быть
  vfxDnUpdateSummary(host);
  pvVoiceTune();}
// Одна строка цепочки: галка «вкл», название и кнопки. Иконки стрелок — SVG
// (эмодзи в хроме запрещены, docs/DESIGN.md), подписи — через t().
// Путь, имя и состояние плагина лежат в data-атрибутах строки: строка И ЕСТЬ запись
// цепочки, поэтому порядок строк — это порядок обработки, и второй копии списка нет.
function vstRow(p){
  const el=document.createElement('div');el.className='setrow';
  el.setAttribute('data-vst','1');
  el.dataset.path=p.path||'';el.dataset.name=p.name||'';el.dataset.state=p.state||'';
  el.innerHTML='<label class="chk" style="flex:0 0 auto" data-t="'
    +esc(t('Плагин в цепочке: снятая галка — плагин пропускается'))+'">'
    +'<input type="checkbox" data-vfx="vst_on"'+(p.on!==false?' checked':'')+' onchange="vstFxOn(this)"></label>'
    +'<span class="grow" style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(vstTitle(p))+'</span>'
    +'<button class="sm" onclick="vstFxEdit(this)" data-t="'
    +esc(t('Окно плагина откроется СВЕРХУ, сразу: ручки слышно на звуке клипа в реальном времени, а настройки сохраняются сами — закрыл окно, и они уже у спикера.'))+'">'+t('Настроить')+'</button>'
    +'<button class="icon" aria-label="'+esc(t('Выше в цепочке'))+'" data-t="'+esc(t('Выше в цепочке'))+'"'
    +' onclick="vstFxMove(this,-1)">'+ico('arrow_up')+'</button>'
    +'<button class="icon" aria-label="'+esc(t('Ниже в цепочке'))+'" data-t="'+esc(t('Ниже в цепочке'))+'"'
    +' onclick="vstFxMove(this,1)">'+ico('arrow_down')+'</button>'
    +'<button class="sm" onclick="vstFxDel(this)">'+t('Убрать')+'</button>';
  return el;}
// Пустая цепочка — не пустое место, а строка словами: иначе непонятно, есть ли
// вообще обработка, и «Добавить плагин» читается как единственный путь.
function vstFxNote(host){
  const box=vfxEl(host,'vst');if(!box)return;
  const rows=box.querySelectorAll('[data-vst]').length;
  const note=box.querySelector('[data-vfx="vst_none"]');
  if(rows){if(note)note.remove();return;}
  if(note)return;
  const d=document.createElement('div');d.className='muted';d.style.fontSize='12px';
  d.setAttribute('data-vfx','vst_none');
  d.textContent=t('плагинов нет — цепочка только из шумодава');
  box.appendChild(d);}
// Включили/выключили шумодав: пересчитаться должно всё, что зависит от обработки, —
// голос клипа (pvVoiceTune) и запечённый трек. Отдельного правила «включили обработку —
// поставить галку» больше нет: выключатель один, и он же решение.
function vstFxDn(el){pvVoiceTune();}
function vstFxOn(el){pvVoiceTune();}
function vstFxDel(el){
  const row=(el&&el.closest)?el.closest('[data-vst]'):null;if(!row)return;
  const host=voiceFxHost(el);row.remove();
  if(host){vstFxNote(host);pvVoiceTune();}}
function vstFxMove(el,d){
  const row=(el&&el.closest)?el.closest('[data-vst]'):null;if(!row)return;
  const box=row.parentElement,rows=Array.from(box.querySelectorAll('[data-vst]'));
  const i=rows.indexOf(row),j=i+d;
  if(i<0||j<0||j>=rows.length)return;
  if(d<0)box.insertBefore(row,rows[j]);else box.insertBefore(row,rows[j].nextSibling);
  pvVoiceTune();}
function vstFxAdd(el){
  const host=voiceFxHost(el);if(!host)return;
  const sel=vfxEl(host,'pick'),box=vfxEl(host,'vst');
  const idx=parseInt(sel&&sel.value,10);
  if(!box||isNaN(idx)||idx<0||idx>=VSTLIST.length){toast(t('Выбери плагин из списка'));return;}
  const p=VSTLIST[idx];
  // Один и тот же плагин дважды в цепочке — это почти всегда промах: у него одно
  // состояние, и вторая копия молча перетрёт первую.
  const have=Array.from(box.querySelectorAll('[data-vst]'))
    .some(r=>r.dataset.path===p.path&&(r.dataset.name||'')===(p.name||''));
  if(have){toast(t('Этот плагин уже в цепочке'));return;}
  box.appendChild(vstRow({path:p.path,name:p.name||'',state:'',on:true}));
  vstFxNote(host);
  pvVoiceTune();}
// Список найденных VST3 грузим при открытии панели или по кнопке «Обновить список».
// Имена читает ОТДЕЛЬНЫЙ процесс (core/voicefx_scan): JUCE оставляет потоки чужого
// плагина жить до конца процесса, поэтому в сервере плагин не грузится никогда.
// Прочитанное ложится в кеш на диске (путь + mtime + размер), так что повторное
// открытие панели не читает НИЧЕГО, а после установки нового плагина досканируется
// только он. `refresh` — кнопка: она заставляет проверить файлы заново, а не
// брать список из кеша памяти.
async function vstFxList(el,refresh){
  const host=voiceFxHost(el);if(!host)return;
  const sel=vfxEl(host,'pick');if(!sel)return;
  sel.innerHTML='<option value="">'+t('ищу плагины…')+'</option>';
  let d;
  try{d=await (await fetch('/api/voicefx_vst_list'+(refresh?'?refresh=1':''))).json();}
  catch(e){sel.innerHTML='<option value="">'+t('список плагинов не пришёл')+'</option>';uiLog('voicefx_vst_list: '+e);return;}
  if(d.error){sel.innerHTML='<option value="">'+t('список плагинов не пришёл')+'</option>';uiLog('voicefx_vst_list: '+errText(d));return;}
  VSTLIST=d.plugins||[];sel.innerHTML='';
  if(!VSTLIST.length){sel.innerHTML='<option value="">'+t('плагинов не нашлось')+'</option>';return;}
  VSTLIST.forEach((p,i)=>{const o=document.createElement('option');o.value=i;o.textContent=vstTitle(p);sel.appendChild(o);});}
// Куда играть живое прослушивание: настройка ЭТОЙ машины, а не спикера, поэтому в
// localStorage, а не в профиле (профиль уезжает на другой компьютер).
// try/catch — localStorage бывает выключен настройками браузера, и падать из-за
// запоминания устройства незачем.
const FX_DEV_KEY='reelsi_fx_device';
function fxDeviceGet(){try{return localStorage.getItem(FX_DEV_KEY)||'';}catch(e){return '';}}
function fxDeviceSet(v){try{if(v)localStorage.setItem(FX_DEV_KEY,v);else localStorage.removeItem(FX_DEV_KEY);}catch(e){uiLog('устройство вывода: '+e);}}
// Список устройств спрашиваем при открытии панели: он нужен и подсказкой «куда
// играть», и вторым пунктом «По умолчанию» (пусто = системное).
async function fxDeviceFill(host){
  const sel=vfxEl(host,'device');if(!sel)return;
  const want=fxDeviceGet();
  sel.innerHTML='<option value="">'+t('По умолчанию')+'</option>';
  let d;
  try{d=await (await fetch('/api/voicefx_devices')).json();}
  catch(e){uiLog('voicefx_devices: '+e);return;}
  if(d.error){uiLog('voicefx_devices: '+errText(d));return;}
  ((d&&d.devices)||[]).forEach(n=>{const o=document.createElement('option');
    o.value=n;o.textContent=n;sel.appendChild(o);});
  // Выбранное устройство могло исчезнуть (наушники выдернули): оставляем
  // «По умолчанию», а не пустую строку в списке.
  sel.value=(want&&Array.from(sel.options).some(o=>o.value===want))?want:'';
  sel.onchange=()=>fxDeviceSet(sel.value);}
// ---- шумодав RoFormer: состояние окружения и установка ----------------------
// RoFormer живёт в СВОЁМ окружении (~/.reelsi/voice_sep, core/voicefx_sep): venv с
// audio-separator и две модели по ~0.9 ГБ. Пока его нет, движок выбрать можно, а
// работать он не будет — поэтому панель спрашивает состояние и показывает кнопку
// установки. Числа в разметке нет: размер загрузки приходит с сервера (он один
// знает и размеры моделей, и что уже скачано).
function vfxSepStep(step){
  if(step==='venv')return t('создаю окружение');
  if(step==='pip')return t('ставлю пакеты audio-separator');
  if(step==='model')return t('качаю модель');
  if(step==='done')return t('готово');
  if(step==='fail')return t('не вышло');
  return '';}
// Строка хода установки: шаг, номер и хвост лога (вывод pip и загрузки).
function vfxSepText(job){
  if(!job||!job.step)return '';
  const step=vfxSepStep(job.step);
  if(job.step==='fail')return t('Установка RoFormer: {step} — {err}',{step:step,err:job.error||''});
  if(job.step==='done')return t('Установка RoFormer: {step}',{step:step});
  const last=Array.isArray(job.log)&&job.log.length?job.log[job.log.length-1]:'';
  const tail=(typeof last==='string'&&last)?' — '+last:'';
  return t('Установка RoFormer: {step} ({i}/{n})',{step:step,i:job.i,n:job.n})+tail;}
// Размер загрузки для кнопки: сервер отдаёт и готовую строку, и число байт —
// считаем по числу, чтобы в английском интерфейсе было «GB», а не «ГБ» (язык
// сервера и язык браузера могут не совпадать).
function vfxSepSize(){
  const b=(VFXSEP&&VFXSEP.size_bytes)?VFXSEP.size_bytes:0;
  if(!b)return (VFXSEP&&VFXSEP.size)||'';
  const gb=Math.ceil(b/1073741824*10)/10;
  return t('~{gb} ГБ',{gb:gb.toFixed(1)});}
// Показать или спрятать кнопку скачивания: она нужна ровно тогда, когда выбран
// движок RoFormer, а окружения или модели для него нет. Кнопка стоит РЯДОМ со
// списком движка и не растягивается: это действие, а не украшение панели.
function vfxSepApply(host){
  if(!host)return;
  const btn=vfxEl(host,'sep_install'),st=vfxEl(host,'sep_state');
  if(!btn)return;
  const eng=vfxEngineSel(host);
  const job=(VFXSEP&&VFXSEP.job)?VFXSEP.job:null;
  const inst=!!(VFXSEP&&VFXSEP.engines&&VFXSEP.engines[eng]&&VFXSEP.engines[eng].installed);
  const busy=!!(job&&job.running);
  const need=(eng==='roformer'||eng==='roformer_aggr')&&(!inst||busy);
  btn.style.display=need?'':'none';
  if(!need){if(st)st.textContent='';return;}
  const size=vfxSepSize();
  btn.textContent=size?t('Скачать RoFormer ({size})',{size:size}):t('Скачать RoFormer');
  btn.disabled=busy;
  if(st)st.textContent=vfxSepText(job);}
async function voiceFxSepFill(host){
  if(!host)return;
  let d;
  try{d=await (await fetch('/api/voicefx_roformer')).json();}
  catch(e){uiLog('voicefx_roformer: '+e);vfxSepApply(host);return;}
  if(d&&d.error){uiLog('voicefx_roformer: '+errText(d));VFXSEP=null;}
  else VFXSEP=d;
  vfxSepApply(host);}
// Установка идёт минутами и уже фоном на сервере: ответ приходит сразу, а ход
// работы панель дочитывает опросом и показывает в ОБЩЕЙ форме прогресса
// (55-progress.js: progOpen/progItem/progDone) — у неё есть проценты, строки шагов
// и хвост лога, а второй такой разметки в интерфейсе быть не должно.
async function voiceFxSepInstall(el){
  const host=voiceFxHost(el);if(!host)return;
  if(el)el.disabled=true;
  let d;
  try{d=await (await fetch('/api/voicefx_roformer_install',{method:'POST'})).json();}
  catch(e){toast(t('Установка не запустилась — сервер не ответил'));uiLog('voicefx_roformer_install: '+e);vfxSepApply(host);return;}
  if(d&&d.error){toast(errText(d));uiLog('voicefx_roformer_install: '+JSON.stringify(d).slice(0,200));vfxSepApply(host);return;}
  progOpen({title:t('Установка RoFormer'),items:[{name:t('окружение и модели RoFormer')}]});
  uiLog(t('ставлю RoFormer — прогресс в общей форме прогресса'));
  voiceFxSepWatch(host);}
// Опрос хода установки: строка в шапке общей формы — шаг и проценты, строка задачи —
// тот же шаг словами. Кнопка на панели при этом остаётся: по ней видно, что установка
// уже идёт (disabled), и её не нажмут второй раз.
function voiceFxSepWatch(host){
  clearTimeout(VFXSEP_TIMER);
  VFXSEP_TIMER=setTimeout(async()=>{
    if(!vfxEl(host,'sep_install'))return;        // панель перерисовали — опрос не наш
    await voiceFxSepFill(host);
    const job=(VFXSEP&&VFXSEP.job)?VFXSEP.job:null;
    if(job){
      progStep(vfxSepText(job));
      progItem(t('окружение и модели RoFormer'),job.step==='fail'?'error':'voice',
        {pct:(job.pct||0)/100,detail:job.step==='fail'?(job.error||''):vfxSepStep(job.step)});
    }
    if(job&&job.running){voiceFxSepWatch(host);return;}
    if(!job)return;
    if(job.step==='fail'){progItem(t('окружение и модели RoFormer'),'error',{reason:job.error||''});
      progDone(job.error||'',true);
      toast(t('RoFormer не установился: {err}',{err:job.error||''}));}
    else{progItem(t('окружение и модели RoFormer'),'done',{detail:t('готово')});
      progDone(t('RoFormer установлен — можно слушать'));
      if(typeof voiceFxStatus==='function')voiceFxStatus(host,'');
      if(typeof vtNote==='function'&&typeof ED!=='undefined')vtNote(ED,'');
      const fx=voiceFxRead(host);
      voiceFxHostSync(fx,{force:true});}
    pvVoiceTune();},VFXSEP_POLL);}
// Окно плагина открывает СЕРВЕР (отдельным процессом: JUCE требует главный поток) — и
// это ПАНЕЛЬ уже звучащего живого хоста клипа (core/voicefx_editor --live), а не
// отдельный процесс со своим звуком. Модель как в Ableton/DaVinci: плагин — вставка на
// дорожке, голос превью ВСЕГДА идёт через него вживую, окно лишь показывает его ручки.
// Открыл и закрыл окно — звук не рвётся; добавил, убрал, включил, переставил плагин —
// цепочка перестраивается на лету (команда `chain`), а нейро-шумодав при этом НЕ
// считается заново: дорожка шумодава лежит в кеше отдельно от плагинов.
//
// Хост поднимается при открытии превью клипа (vtPrep → voiceFxHostSync), пока в цепочке
// есть включённые плагины, и гаснет, когда превью закрыли, открыли другой клип или
// плагины выключили. Позицию хосту задаёт плеер теми же командами, что и у себя
// (play/seek/pause — 60-preview.js:vtLiveUpdate). Настройки сохраняются САМИ: закрыл
// окно — состояние плагина уехало в профиль спикера (сервер, api/voicefx.py:_save_live),
// а панель забирает его опросом. Отдельной кнопки «Сохранить у спикера» нет.
//   VFXHOST.key   — что хост знает о цепочке (по нему решаем, слать ли `chain`);
//   VFXHOST.seq   — счётчик «поколений»: погашенный хост не оживает от запоздавшего ответа;
//   VFXHOST.row   — строка цепочки, чьё окно открыли (туда вернётся состояние плагина);
//   VFXHOST.state — состояние плагина, уже принятое от сервера (без повторных тостов).
let VFXHOST={timer:0,key:'',seq:0,starting:null,row:null,state:'',skip:''};
// Ключ цепочки: путь, имя, галка и состояние. Состояние в ключе НУЖНО: правка ручки в
// окне меняет строку панели, и без него ключ не менялся бы, а хост о ней не узнал.
function voiceFxHostKey(fx){
  const f=(fx&&Array.isArray(fx.vst))?fx.vst:[];
  return JSON.stringify(f.map(v=>[v.path||'',v.name||'',v.on!==false,v.state||'']));}
async function voiceFxHostPost(url,body){
  try{return await (await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})).json();}
  catch(e){uiLog(url+': '+e);return {error:String(e)};}}
// Одна дверь «привести хост в соответствие с панелью»: нужен ли он, поднят ли, знает ли
// актуальную цепочку. Её зовут и открытие превью, и любая правка ручек (vtPrep), и
// «Настроить». `opts.window` — хост нужен окну, даже если ни один плагин не включён
// (правят выключенный, чтобы его потом включить); `opts.force` — слать цепочку, даже
// если ключ тот же (перед окном: строку могли добавить меньше чем за затишье ручек).
async function voiceFxHostSync(fx,opts){
  const o=opts||{};
  const P=o.player||(typeof ED!=='undefined'?ED:null);
  if(!P||!P.xml||!vtCam1(P))return false;
  if(!voiceFxHasVst(fx)&&!o.window&&!voiceFxWindowOn()){
    // Плагинов не осталось: хост не нужен, превью играет дорожку шумодава в браузере.
    if(voiceFxHostOn()||VFXHOST.starting)await voiceFxHostStop();
    return false;}
  const key=voiceFxHostKey(fx);
  if(voiceFxHostOn()){
    if(o.force||key!==VFXHOST.key){
      VFXHOST.key=key;
      await voiceFxHostPost('/api/voicefx_live',{sid:VOICEFXLIVE.sid,cmd:'chain',fx:fx||{}});}
    return true;}
  return voiceFxHostStart(fx,key,P);}
// Поднять хост: без окна, на месте бегунка. Второй заход, пока первый идёт, ждёт первого
// (иначе два процесса на один клип — голос звучал бы дважды).
function voiceFxHostStart(fx,key){
  const P=(arguments.length>2&&arguments[2])||(typeof ED!=='undefined'?ED:null);
  if(!P)return Promise.resolve(false);
  if(typeof voiceFxStatus==='function')voiceFxStatus($('pvvoice'),'');
  if(typeof vtNote==='function')vtNote(P,'');
  if(VFXHOST.starting)return VFXHOST.starting;
  const seq=++VFXHOST.seq,xml=P.xml,src=vtCam1(P);
  const at=vtSrcAt(P,typeof vtNow==='function'?vtNow(P):0);
  // Громкость задания — итог ОБОИХ ползунков (одна формула, voiceFxLiveOutDb): хост
  // поднимается уже с той громкостью, что человек слышит в превью, и голос не «прыгает»
  // при первом же пересчёте.
  const gain=(typeof voiceFxLiveOutDb==='function')?voiceFxLiveOutDb():0.0;
  VFXHOST.starting=(async()=>{
    const d=await voiceFxHostPost('/api/voicefx_host',{xml:xml,src:src,fx:fx||{},
      start:(at==null?0:Math.max(0,at)),device:fxDeviceGet(),speaker:VOICEFXSPK,gain_db:gain});
    if(seq===VFXHOST.seq)VFXHOST.starting=null;
    if(!d||!d.ok||!d.sid){
      if(d&&d.error)uiLog('voicefx_host: '+String(d.error).slice(0,200));
      if(seq===VFXHOST.seq&&d&&d.error&&typeof voiceFxStatus==='function')
        voiceFxStatus($('pvvoice'),'⚠ '+errText(d));
      return false;}
    if(seq!==VFXHOST.seq||P.xml!==xml){
      // Пока хост поднимался, превью закрыли или открыли другой клип — он уже лишний.
      voiceFxHostPost('/api/voicefx_host_stop',{sid:d.sid,xml:xml});return false;}
    // Хост уже был на сервере (страницу перезагрузили): цепочку он знает по прежнему
    // заданию, поэтому ключ пуст, и следующая сверка пошлёт актуальную.
    VFXHOST.key=d.fresh?key:'';VFXHOST.state='';VFXHOST.skip='';
    if(typeof voiceFxStatus==='function')voiceFxStatus($('pvvoice'),'');
    if(typeof vtNote==='function')vtNote(P,'');
    VOICEFXLIVE={sid:d.sid,running:true,track_ready:!!d.track_ready,window:!!d.window,
      skipped:d.skipped||[],audio_error:d.audio_error||'',xml:xml,track_input:d.track_input||d.input||''};
    uiLog('живой звук: плагины играют вживую, окно — их панель');
    voiceFxHostNotes(d);
    voiceFxHostPoll(P);
    return true;})();
  return VFXHOST.starting;}
// Опрос хоста: жив ли, досчитана ли дорожка, открыто ли окно, что пропущено и не пришло
// ли новое состояние плагина. 500 мс — не звук, а факт; заодно это «пульс» для сервера
// (api/voicefx.py снимает хост, если страница замолчала).
function voiceFxHostPoll(){
  clearTimeout(VFXHOST.timer);VFXHOST.timer=0;
  const L=VOICEFXLIVE;if(!L||!L.sid)return;
  const P=(arguments.length>0&&arguments[0])||(typeof ED!=='undefined'?ED:null);
  VFXHOST.timer=setTimeout(async()=>{
    VFXHOST.timer=0;
    const sid=L.sid;
    let d;
    try{d=await (await fetch('/api/voicefx_live?sid='+encodeURIComponent(sid))).json();}
    catch(e){voiceFxHostPoll(P);return;}        // сервер не ответил — попробуем ещё
    if(!VOICEFXLIVE||VOICEFXLIVE.sid!==sid)return;   // хост погашен или сменился, пока ждали
    if(!d.ok||!d.running){voiceFxHostGone(d,P);return;}
    const was=VOICEFXLIVE;
    VOICEFXLIVE={sid:sid,running:true,track_ready:!!d.track_ready,window:!!d.window,
      skipped:d.skipped||[],audio_error:d.audio_error||'',xml:was.xml,track_input:d.track_input||d.input||''};
    voiceFxHostNotes(d);
    if(P&&P.vt&&(!!d.track_ready!==!!was.track_ready||d.track_input!==was.track_input||
        !!d.audio_error!==!!was.audio_error)){
      // Звук перешёл к хосту (или вернулся к странице, в том числе из-за сбоя звука
      // хоста): команды считаются с нуля — хост мог стоять на паузе, а страница думать,
      // что он играет. Тик тут же расставит глушение и пошлёт пуск или паузу.
      const st=vtOf(P);st.live=null;
      if(typeof vtNow==='function')vtTick(P,vtNow(P));}
    if(d.state&&d.state!==VFXHOST.state){
      // Окно закрыли: состояние плагина уже у спикера (сервер записал), а в строку цепочки
      // оно возвращается здесь — иначе следующее автосохранение панели вернуло бы прежнее.
      VFXHOST.state=d.state;
      const row=VFXHOST.row;
      if(row&&row.isConnected)row.dataset.state=d.state;
      toast(t('Настройки плагина сохранены'));}
    if(was.window&&!d.window){
      // Окно закрыто: если плагинов больше нет в звуке, хост можно погасить.
      const host=$('pvvoice');voiceFxHostSync(host?voiceFxRead(host):null,{player:P});}
    voiceFxHostPoll(P);},500);}
// Что не так с хостом — словами в панель: плагин не загрузился (с ИМЕНЕМ) или звук
// хоста не идёт (устройство не приняло частоту, поток отвалился). Строка одна и
// пишется только когда что-то изменилось, иначе опрос затирал бы ход голоса, который
// рисует vtNote.
// Сбой звука ВАЖНЕЕ пропущенного плагина: пока он есть, превью играет свой голос, и
// человеку нужно знать, почему плагины «не слышно».
function voiceFxHostNotes(d){
  const sk=Array.isArray(d&&d.skipped)?d.skipped:[];
  const err=String((d&&d.audio_error)||'');
  const key=sk.map(s=>(s&&s.name)||'').join('|')+'|'+err;
  if(key===VFXHOST.skip)return;
  VFXHOST.skip=key;
  if(typeof voiceFxStatus!=='function')return;
  if(err){
    voiceFxStatus($('pvvoice'),t('живой звук плагинов не работает: {err}',{err:err}));
    uiLog('voicefx_host: живой звук плагинов не работает — '+err);
    return;}
  if(!sk.length)return;
  voiceFxStatus($('pvvoice'),sk.map(s=>t('{n} не загрузился — пропущен',{n:(s&&s.name)||''})).join('; '));
  uiLog('voicefx_host: '+sk.map(s=>((s&&s.name)||'')+' — '+((s&&s.reason)||'')).join('; '));}
// Хост ушёл сам (упал, завершился: ни один плагин не загрузился): звук возвращается
// странице. Заново его не поднимаем — это была бы петля; следующая правка ручки или
// открытие превью поднимут его снова.
function voiceFxHostGone(d,player){
  clearTimeout(VFXHOST.timer);VFXHOST.timer=0;
  const P=player||(typeof ED!=='undefined'?ED:null);
  VOICEFXLIVE=null;VFXHOST.key='';VFXHOST.row=null;
  voiceFxHostNotes(d);
  if(d&&d.ok&&d.error&&typeof voiceFxStatus==='function')
    voiceFxStatus($('pvvoice'),t('Окно плагина закрылось с ошибкой: {err}',{err:d.error}));
  if(P&&P.vt&&typeof vtNow==='function')vtTick(P,vtNow(P));}
// ОДНА формула «что слышно из живого хоста»: громкость голоса спикера (дБ) плюс
// громкость прослушивания (<video>/<audio> у страницы — множитель MEDIA_VOL, см.
// 60-preview.js:applyMediaVol). Хост играет СВОИМ процессом, мимо страницы, и без
// этого ползунок громкости прослушивания его не касался бы вовсе. Второй копии
// формулы нет: её зовут и правка любого из ползунков, и подъём хоста.
function voiceFxLiveOutDb(){
  const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
  const vdb=s.voice_db!=null?+s.voice_db:0.;
  if(isNaN(vdb))return 0.;
  const vol=(typeof MEDIA_VOL==='number'&&isFinite(MEDIA_VOL))?Math.max(0,Math.min(1,MEDIA_VOL)):1;
  // Ползунок на нуле — тишина, а не «очень тихо»: Math.log10(0) даёт −Infinity, и
  // JSON.parse на сервере превратил бы её в null. −120 дБ хост считает за тишину.
  if(vol<=0)return -120.;
  return vdb+20*Math.log10(vol);}
// Громкость живого хоста на лету: шлём команду gain в фоновый процесс
function voiceFxLiveGain(){
  if(voiceFxHostOn()&&VOICEFXLIVE&&VOICEFXLIVE.sid){
    voiceFxHostPost('/api/voicefx_live',{sid:VOICEFXLIVE.sid,cmd:'gain',db:voiceFxLiveOutDb()});}}
// Погасить хост: превью закрыли, клип сменили, плагины выключили. Процесс снимает сервер
// по PID (api/voicefx.py:api_voicefx_host_stop). Своё состояние чистим сразу, не дожидаясь
// ответа: запоздавший ответ погашенного хоста (`seq`) уже не оживит.
async function voiceFxHostStop(){
  clearTimeout(VFXHOST.timer);VFXHOST.timer=0;
  const L=VOICEFXLIVE;
  VOICEFXLIVE=null;VFXHOST.key='';VFXHOST.row=null;VFXHOST.state='';VFXHOST.skip='';
  VFXHOST.seq++;VFXHOST.starting=null;
  // Снять глушение камеры — на ЕДИНСТВЕННОМ плеере шага 1 (редакторе): монтажного
  // плеера PV с его состоянием воспроизведения больше нет.
  if(typeof ED!=='undefined'&&ED.vt&&typeof vtNow==='function')vtTick(ED,vtNow(ED));
  if(typeof IPV!=='undefined'&&IPV.vt&&typeof vtNow==='function')vtTick(IPV,vtNow(IPV));
  if(typeof CPV!=='undefined'&&CPV.vt)vtTick(CPV,typeof cpvNow==='function'?cpvNow():(typeof vtNow==='function'?vtNow(CPV):0));
  if(!L||!L.sid)return;
  await voiceFxHostPost('/api/voicefx_host_stop',{sid:L.sid,xml:L.xml||''});}
// Страницу закрыли или перезагрузили — хост гасим маячком: fetch на выгрузке не
// гарантирован, а без этого процесс висел бы до конца работы сервера (его добьёт и
// молчание опроса, но через минуты).
if(typeof window!=='undefined'&&typeof window.addEventListener==='function')window.addEventListener('pagehide',()=>{
  const L=VOICEFXLIVE;if(!L||!L.sid||!navigator.sendBeacon)return;
  try{navigator.sendBeacon('/api/voicefx_host_stop',
    new Blob([JSON.stringify({sid:L.sid,xml:L.xml||''})],{type:'application/json'}));}catch(e){}});
async function vstFxEdit(el){
  const row=(el&&el.closest)?el.closest('[data-vst]'):null;if(!row)return;
  const host=voiceFxHost(el),box=row.parentElement;
  if(typeof voiceFxStatus==='function')voiceFxStatus(host,'');
  if(typeof vtNote==='function'&&typeof ED!=='undefined')vtNote(ED,'');
  const idx=Array.from(box.querySelectorAll('[data-vst]')).indexOf(row);
  const src=vtCam1(ED);
  if(!src){toast(t('Клип не выбран — окно без звука'));return;}
  toast(t('Открываю окно плагина…'));
  // Хост нужен окну, даже если плагин выключен: цепочку шлём как есть, окно откроется в
  // уже звучащем процессе — звук не прерывается, процесс не перезапускается.
  const fx=voiceFxRead(host)||{};
  const ok=await voiceFxHostSync(fx,{window:true,force:true});
  if(!ok||!VOICEFXLIVE){toast(t('Окно плагина не открылось'));return;}
  VFXHOST.row=row;
  const d=await voiceFxHostPost('/api/voicefx_host_edit',
    {sid:VOICEFXLIVE.sid,index:idx,path:row.dataset.path||''});
  if(d.error){toast(errText(d));uiLog('voicefx_host_edit: '+JSON.stringify(d).slice(0,200));return;}
  VOICEFXLIVE.window=true;
  uiLog('окно плагина открыто — панель звучащего хоста, ручки слышны сразу');}
// Панель «Голос» превью нарезки: единственное место, где обработка голоса
// настраивается. Спикер — ТЕГ КЛИПА (job.speaker), как у LUT и рамки: у клипа без
// тега профиля нет, и крутить нечего (значения живут только в профиле).
function pvVoicePanel(){
  const host=$('pvvoice');if(!host)return;
  const c=(typeof ED!=='undefined'&&ED.xml&&typeof clipByXml==='function')?clipByXml(ED.xml):null;
  const key=(c&&c.job&&c.job.speaker)||'';
  VOICEFXSPK=key;
  const prof=key?(SPEAKERS[key]||null):null;
  const lbl=$('pvvoicespk');if(lbl)lbl.textContent=prof?(prof.label||key):'';
  vtStop(ED);   // новый клип — прежняя дорожка обработанного голоса не наша
  // (хост плагинов гасит сам vtStop: клип другой — звук идёт по нему, а не по прежнему)
  if(!key){voiceFxRender(host,null,{mode:'off'});return;}
  if(!prof){
    voiceFxRender(host,null,{mode:'off',
      note:t('Профиль спикера «{n}» не нашёлся в speakers/ — обработка голоса настраивается в его профиле.',{n:key})});
    return;}
  voiceFxRender(host,prof.voice_fx,{mode:'panel'});
  vstFxList(host,false);
  voiceFxSepFill(host);       // есть ли окружение RoFormer: от этого — кнопка скачивания
  // Включённый ИИ-шумодав работает и в превью: голос ВСЕГО клипа считается сразу при
  // открытии, пока играет звук камеры (vtPrep, 60-preview.js). Выключен — трек
  // клипа убирается, и превью играет звук камеры как есть.
  // Заказ возвращаем: openEditClip ждёт панель, и по её концу видно, что голос уже
  // запрошен (гонки «открыли превью — а трек ещё не заказан» не остаётся).
  return vtPrep(ED);
}
// --------------------------------------------------------------------------- #
// Сохранение настроек голоса — САМО, без кнопки
// --------------------------------------------------------------------------- #
// Две двери, и обе ведут в одно место — профиль спикера клипа:
//   * ручки панели (шумодав, движок, цепочка) — после ЗАТИШЬЯ (`pvVoiceAuto`);
//   * окно плагина — по его закрытию, на СЕРВЕРЕ (`api/voicefx.py:_save_live`):
//     состояние плагина знает только процесс окна, и забирать его фронту незачем.
// Запись профиля у обеих дверей ОДНА — `pvVoiceSave` (свежий профиль с сервера,
// правка ТОЛЬКО voice_fx): вторая копия «как сохранить профиль» разъехалась бы.
// Затишье: ползунок сыплется на каждый пиксель, а сохранение — это файл профиля.
const VFX_SAVE_QUIET=700;
let VFXSAVET=0;
function pvVoiceAuto(){
  clearTimeout(VFXSAVET);
  VFXSAVET=setTimeout(()=>{VFXSAVET=0;
    // Окно плагина открыто: там свои настройки, и записывать сейчас нечего — иначе
    // автосохранение панели перетёрло бы состояние, которое вот-вот отдаст окно.
    if(voiceFxWindowOn())return;
    pvVoiceSave(null,{bake:true});},VFX_SAVE_QUIET);}
// Записать профиль под ТЕКУЩИЕ значения панели. Профиль читаем СВЕЖИМ с сервера и
// правим в нём ОДНО поле: правки чужих полей (LUT, рамка кадра, папки), сделанные
// в соседних панелях, не затираются. `bake` — пересчитать ли голос клипа сразу:
// при закрытии окна плагина это делает сервер, и второй счёт не нужен.
async function pvVoiceSave(btn,opts){
  const o=opts||{};
  const host=o.host||voiceFxHost(btn)||$('pvvoice'),key=VOICEFXSPK;
  if(!host||host.dataset.vfxmode!=='panel')return;
  if(!key){toast(t('У клипа нет спикера — сохранять некуда'));return;}
  // Пустая панель и профиль без обработки: сохранять нечего (профиль не должен
  // обзаводиться полем voice_fx от одного открытия превью).
  if(voiceFxRead(host)==null&&!(SPEAKERS[key]&&SPEAKERS[key].voice_fx))return;
  const fx=voiceFxRead(host);
  voiceFxStatus(host,t('сохраняю профиль…'));
  let cur;
  try{cur=await (await fetch('/api/speakers')).json();}
  catch(e){voiceFxStatus(host,t('профиль не сохранён — сервер не ответил'));uiLog('savespeaker(voice_fx): '+e);return;}
  const prof=(cur&&cur.speakers&&cur.speakers[key])||SPEAKERS[key];
  if(!prof){voiceFxStatus(host,t('профиль спикера не найден — сохранять некуда'));return;}
  const data=JSON.parse(JSON.stringify(prof));
  if(fx)data.voice_fx=fx;else delete data.voice_fx;
  let d;
  try{d=await (await fetch('/api/savespeaker',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:key,data:data})})).json();}
  catch(e){voiceFxStatus(host,t('профиль не сохранён — сервер не ответил'));uiLog('savespeaker(voice_fx): '+e);return;}
  if(!d.ok){voiceFxStatus(host,errText(d)||t('Профиль не сохранён'));return;}
  SPEAKERS[d.key||key]=data;
  voiceFxStatus(host,t('сохранено у спикера «{n}»',{n:data.label||key}));
  uiLog(t('обработка голоса спикера «{n}» сохранена: ',{n:data.label||key})+voiceFxSummary(fx||{}));
  if(o.bake!==false)pvVoiceBake();}   // .voice.wav для ЭТОГО клипа — в фоне (60-preview.js)
async function saveSpeaker(){
  const label=val('spk_label').trim();
  if(!label){toast(t('Дай спикеру имя'));$('spk_label').focus();return;}
  // Правим КОПИЮ профиля, а не собираем с нуля: в файле есть поля, которых в окне нет
  // (ref, breath_model) — пересборка их бы стёрла.
  const data=SPKEDIT?JSON.parse(JSON.stringify(SPEAKERS[SPKEDIT])):{};
  data.label=label;data.outdir=val('spk_outdir').trim();data.jsxdir=val('spk_jsxdir').trim();data.renderdir=val('spk_renderdir').trim();data.style=val('spk_style');
  // Формат кадра: пустой или дефолтный — поля в профиле нет (как у lut и voice_fx):
  // «профиль без правок» не должен обзаводиться записью про формат, и старый
  // ролик собирается ровно как собирался.
  {
    const f=val('spk_format').trim();
    if(!f||f===SPKDEF_FORMAT)delete data.format;else data.format=f;
  }
  data.hint=val('spk_hint');
  // Приписки к промптам генерации картинок (pa/pb — подложка).
  // Пустой слот не пишем, как и раньше: профиль без правок остаётся без image_prompts.
  const exA=val('spk_extra_a').trim(),exB=val('spk_extra_b').trim();
  const exPA=val('spk_extra_pa').trim(),exPB=val('spk_extra_pb').trim();
  const imgPr={};
  if(exA)imgPr.a={extra:exA,pos:val('spk_pos_a')==='prefix'?'prefix':'suffix'};
  if(exB)imgPr.b={extra:exB,pos:val('spk_pos_b')==='prefix'?'prefix':'suffix'};
  if(exPA)imgPr.pa={extra:exPA,pos:val('spk_pos_pa')==='prefix'?'prefix':'suffix'};
  if(exPB)imgPr.pb={extra:exPB,pos:val('spk_pos_pb')==='prefix'?'prefix':'suffix'};
  if(exA||exB||exPA||exPB)data.image_prompts=imgPr;else delete data.image_prompts;
  // Видео хранит отдельные приписки: image_prompts нельзя переиспользовать, иначе
  // «3d icon» случайно уезжает в ролик. Пустые оба слота не записываем, чтобы старый
  // профиль оставался эквивалентен чистому query.
  const videoExA=val('spk_video_extra_a').trim(),videoExB=val('spk_video_extra_b').trim();
  if(videoExA||videoExB){
    data.video_prompts={};
    if(videoExA)data.video_prompts.a={extra:videoExA,pos:val('spk_video_pos_a')==='prefix'?'prefix':'suffix'};
    if(videoExB)data.video_prompts.b={extra:videoExB,pos:val('spk_video_pos_b')==='prefix'?'prefix':'suffix'};
  }else delete data.video_prompts;
  // Обработку голоса это окно НЕ трогает: поле voice_fx пишет только панель «Голос»
  // в превью нарезки (там её слышно). Копия профиля уже принесла voice_fx из файла —
  // пересборка полей окна до него не доходит, и присваивания тут нет нарочно.
  // Папки камер: пустое поле не пишется — «профиль без правок» не получает
  // мусорные camdirs, иначе у всех, кто не трогал, «свои» папки сломали бы автоподбор.
  const cds=[];let hasCam=false;
  for(let k=0;k<nCams();k++){const c=val('spk_camdir'+k).trim();cds.push(c);if(c)hasCam=true;}
  if(hasCam)data.camdirs=cds;else delete data.camdirs;
  // LUT камер: ключ — номер камеры с 1, как в профиле (speakers.py). Пустое поле
  // не пишется: «профиль без правок» не должен обзавестись пустой таблицей.
  const luts={};
  for(let k=0;k<nCams();k++){const c=val('spk_lut'+k).trim();if(c)luts[String(k+1)]=c;}
  if(Object.keys(luts).length)data.lut=luts;else delete data.lut;
  const note=val('spk_note').trim();if(note)data.note=note;else delete data.note;
  const insPhotoStr=val('spk_ins_photo').trim(),insVideoStr=val('spk_ins_video').trim();
  if(!insPhotoStr&&!insVideoStr){
    delete data.inserts;
  }else{
    const insObj={};
    if(insPhotoStr){
      const v=parseInt(insPhotoStr,10);
      if(!/^\d+$/.test(insPhotoStr)||isNaN(v)||v<0||v>30){
        toast(t('Вставок-фото: число от 0 до 30'));return;
      }
      insObj.photo=v;
    }
    if(insVideoStr){
      const v=parseInt(insVideoStr,10);
      if(!/^\d+$/.test(insVideoStr)||isNaN(v)||v<0||v>30){
        toast(t('Вставок-видео: число от 0 до 30'));return;
      }
      insObj.video=v;
    }
    data.inserts=insObj;
  }
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
    if(want==='__custom__')ensureCustomOption();sel.value=(want==='__custom__')?'__custom__':(STYLES[want]?want:'base');sel.dataset.prev=sel.value;onStyleChange();}
  catch(e){toast(t('Стили пришли, но не применились — смотри журнал'));uiLog(t('loadStyles(применение): ')+e);}}
// Смысл intro_y2 сменился: была добавка к intro_y, стала само положение интро на камере 2
// (core/styles.py, migrate_intro_pos2 — то же правило). Стиль без метки intro_pos2_v — старый:
// intro_y2 = intro_y + intro_y2. Сервер переводит стили из /api/styles сам; здесь — копии,
// которые живут в браузере (кастом в localStorage, копия стиля в задании клипа) и до сервера
// доходят только на сборке. Без этого панель показала бы старое число в новом смысле, а правка
// поля потом сложилась бы с intro_y второй раз.
function stMigrateIntroPos2(st){
  if(!st||typeof st!=='object'||st.intro_pos2_v!=null)return st;
  const y=+st.intro_y||0,y2=+st.intro_y2||0;
  if(y||y2)st.intro_y2=y+y2;
  st.intro_pos2_v=2;
  return st;}
function stMigrateCam2Zoom(st){
  if(!st||typeof st!=='object'||st.cam2_zoom!=null)return st;
  if(st.cam2_zoom_on===true){
    st.cam2_zoom=st.cam1_zoom||'pulse';
    const keys=['zoom_start','zoom_big','zoom_lo','zoom_hi','drift_lo','drift_hi',
                'take_zoom','take_min','take_lo','take_hi','take_hold','take_yellow',
                'take_out','yellow_zoom'];
    keys.forEach(k=>{if(st['cam1_'+k]!==undefined)st['cam2_'+k]=st['cam1_'+k];});
  }else{
    st.cam2_zoom='none';
  }
  delete st.cam2_zoom_on;
  return st;}
// У интро камеры 2 свои ручки: галка «интро едет с камерой» (intro_cam2) и точка
// масштабирования (intro_scale_anchor2). Раньше ими правили ключи камеры 1, поэтому
// старый стиль без этих ключей берёт их значения (core/styles.py, migrate_intro_cam2 —
// то же правило). Иначе дефолт BASE поменял бы вид уже собранных стилей: у стиля с
// intro_cam=false интро на перебивке вдруг поехало бы за камерой 2.
// Копии здесь — те, что живут в браузере (кастом в localStorage, стиль в задании клипа)
// и до сервера доходят только на сборке.
function stMigrateIntroCam2(st){
  if(!st||typeof st!=='object')return st;
  if(st.intro_cam2==null)st.intro_cam2=(st.intro_cam==null)?true:!!st.intro_cam;
  if(st.intro_scale_anchor2==null)st.intro_scale_anchor2=st.intro_scale_anchor||'comp';
  return st;}
function stMigrateCamZoom(st){
  if(!st||typeof st!=='object'||(st.cam_zoom_v!=null && st.cam_zoom_v>=3))return st;
  const v = st.cam_zoom_v || 0;
  if(v < 2){
    ['cam1','cam2'].forEach(prefix=>{
      if(st[prefix+'_take_yellow_mode']===undefined){
        const oldY=st[prefix+'_take_yellow'];
        st[prefix+'_take_yellow_mode']=(oldY===true)?'snap':'off';
      }
      st[prefix+'_take_yellow']=(st[prefix+'_take_yellow_mode']==='snap'||st[prefix+'_take_yellow_mode']==='only');
      const zMode=st[prefix+'_zoom'];
      if(zMode==='none'){
        st[prefix+'_zoom_start']=false;
      }
      if(st[prefix+'_take_zoom']===true&&zMode!=null&&zMode!=='jump'){
        st[prefix+'_take_zoom']=false;
      }
    });
  }
  ['cam1','cam2'].forEach(prefix=>{
    if(st[prefix+'_yellow_zoom']===undefined){
      const takeZ = !!st[prefix+'_take_zoom'];
      const ym = st[prefix+'_take_yellow_mode'];
      if(ym === 'snap'){
        st[prefix+'_yellow_zoom'] = takeZ;
      }else if(ym === 'only'){
        st[prefix+'_yellow_zoom'] = takeZ;
        st[prefix+'_take_zoom'] = false;
      }else if(ym === 'off'){
        st[prefix+'_yellow_zoom'] = false;
      }else if(st[prefix+'_take_yellow']!==undefined){
        st[prefix+'_yellow_zoom'] = !!st[prefix+'_take_yellow'] && takeZ;
      }else{
        st[prefix+'_yellow_zoom'] = false;
      }
    }
    if(st[prefix+'_take_out']===undefined){
      st[prefix+'_take_out'] = 2.4;
    }
  });
  st.cam_zoom_v=3;
  return st;}
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
    stMigrateIntroPos2(j.style);
    stMigrateCam2Zoom(j.style);
    stMigrateIntroCam2(j.style);
    stMigrateCamZoom(j.style);
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
function addCustomStyle(){const src=CURSTYLE||STYLES.base||{};CURSTYLE=JSON.parse(JSON.stringify(src));CURSTYLE.label='кастом';ensureCustomOption();$('style').value='__custom__';$('style').dataset.prev='__custom__';
  STYLE_EDITING=null;STYLE_EDIT_ORIG=null;STYLE_TOUCHED=false;
  if($('st_name'))$('st_name').value='';onStyleChange();}
const BUILTIN_STYLES={base:1,geologica:1};   // живут в styles.py, файла в styles/ у них нет
// Правка ШАБЛОНА. Раньше карандаш переключал селектор на «кастом (свой)» — и человеку
// казалось, что он заводит новый стиль, хотя сохранение перезаписывало тот же файл
// Теперь правка выглядит правкой: селектор показывает имя шаблона с
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
  $('style').dataset.prev='__edit__';
  // Рото — теперь поле СТИЛЯ, а не настройка клипа: шаблон его и приносит,
  // fillStyleFields раскладывает по панели вместе с остальными. Прежняя возня с
  // #roto/#rotobottom (запомнить у клипа и вернуть) канула вместе со старой разметкой.
  if($('st_name'))$('st_name').value=BUILTIN_STYLES[key]?'':key;
  const el=$('st_saved');if(el){el.className='ok';el.textContent='';}
  fillStyleFields();
  renderStyleInfo();
  if(BUILTIN_STYLES[key])toast(t('«{n}» — встроенный шаблон: сохрани правки под своим именем',{n:t(src.label||key)}));
  else if($('stylehint'))$('stylehint').textContent=t('правится шаблон «{n}» — «Сохранить» перезапишет его',{n:t(src.label||key)});}
async function delStyle(){let name=$('style').value;
  // В режиме правки шаблона в селекторе стоит служебный `__edit__`, а корзина
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

// Стиль клипа изменён: пометка флагом при редактировании не шаблона,
// обновление кнопки сохранения и точек расхождения.
function styleTouched(){
  if(typeof STYLE_TOUCHED!=='undefined'&&(typeof STYLE_EDITING==='undefined'||!STYLE_EDITING))STYLE_TOUCHED=true;
  if(typeof updateStyleSaveUI==='function')updateStyleSaveUI();
  if(typeof updateStyleDiffDots==='function')updateStyleDiffDots();
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
async function onStyleChange(){const sel=$('style');const prev=(sel&&sel.dataset.prev)||sel.value;const name=sel.value;const custom=$('stylecustom');
  if(name!==prev&&styleDirty()){
    sel.value=prev;
    if(!await askConfirm(t('На стиле есть несохранённые правки. Сменить стиль без сохранения?'))){
      return;
    }
    sel.value=name;
  }
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
    STYLE_EDITING=null;STYLE_EDIT_ORIG=JSON.parse(JSON.stringify(STYLES[name]||{}));STYLE_TOUCHED=false;
    CURSTYLE=JSON.parse(JSON.stringify(STYLES[name]||{}));
    if(custom)custom.style.display='';
    if($('st_name'))$('st_name').value='';
    if($('stylehint'))$('stylehint').textContent='';
    const el=$('st_saved');if(el){el.className='ok';el.textContent='';}
    fillStyleFields();
  }
  if(sel)sel.dataset.prev=sel.value;
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
    const b=stReadView('roto_bottom');if(b!=null)CURSTYLE.roto_bottom=stStore(findFieldByKey('roto_bottom'),b);
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
      m._pct=pct;   // % от высоты ИСХОДНИКА: с рамкой кадра полосу пересчитывает ipvRotoMaskZoom
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
  const pv=$('pvvoicedb');if(pv)pv.value=Math.round((s.voice_db!=null?s.voice_db:0)*2)/2;
  const pvv=$('pvvoicedbv');if(pvv){
    const val=(s.voice_db!=null?s.voice_db:0);
    pvv.textContent=(val>=0?'+':'')+val.toFixed(1)+' dB';}
  if(typeof syncSldnums==='function')syncSldnums();
  updateStyleDiffDots();}
function setStyleDb(which,v){if(!CURSTYLE)CURSTYLE=JSON.parse(JSON.stringify(STYLES.base||{}));
  const db=Math.max(-60,Math.min(12,Math.round((parseFloat(v)||0)*2)/2));
  if(which==='music'){CURSTYLE.music_db=db;}
  else{CURSTYLE.voice_db=db;}
  if(typeof stRefresh==='function')stRefresh(which==='music'?'music_db':'voice_db');
  syncDbSliders();applyDbGains();
  if(typeof styleTouched==='function')styleTouched();
  captureAE();}
// Точка наезда Камеры 1 прицелом (часть 4): кнопка ставит курсор в crosshair
// над кадром предпросмотра, клик кладёт точку в cam1_zoom_cx/cy (доли кадра), на кадре
// остаётся маркер-перекрестие. Повторное нажатие кнопки и Esc — отмена. Маркер виден
// только когда точку ПРАВЯТ: в режиме прицела или на наведении/фокусе на кнопке. Раньше
// висел всегда — «зачем он на кадре» (жалоба 2026-08-13); потерять поставленную точку
// нельзя и так: она держит зум, и клик по кнопке снова её показывает.
// ZOOM_PICK хранит ЦЕЛЬ: false | 'auto' | 'cam1' | 'cam2'. 'auto' — камера, что видна в
// кадре сейчас (см. zoomPickTarget), явная цель — кнопка «Прицел» у поля именно этой камеры.
// ZOOM_HOVER остаётся булевым (наведение на кнопку), а камера наведения — в ZOOM_HOVER_CAM.
let ZOOM_PICK=false;
let ZOOM_HOVER=false;
let ZOOM_HOVER_CAM='auto';
// V4: кнопку st_pickzoom панель создаёт ПОСЛЕ fetch('/api/style_schema'), а
// DOMContentLoaded стреляет раньше — обработчик на саму кнопку не вешался никогда.
// Делегируем с контейнера панели. mouseenter/mouseleave НЕ всплывают, поэтому их
// слушаем в фазе перехвата (capture): иначе до контейнера событие не дойдёт.
document.addEventListener('DOMContentLoaded',()=>{
  const host=document.getElementById('stpanel')||document.body;
  ['mouseenter','focusin'].forEach(ev=>host.addEventListener(ev,(e)=>{
    if(e.target&&(e.target.id==='st_pickzoom'||e.target.id==='st_pickzoom2')){ZOOM_HOVER=true;ZOOM_HOVER_CAM=(e.target.id==='st_pickzoom2')?'cam2':'auto';zoomPickMark();}
  },{capture:true}));
  ['mouseleave','focusout'].forEach(ev=>host.addEventListener(ev,(e)=>{
    if(e.target&&(e.target.id==='st_pickzoom'||e.target.id==='st_pickzoom2')){ZOOM_HOVER=false;zoomPickMark();}
  },{capture:true}));
});
// Чью точку правит клик: 'cam2' — если Камера 2 активна И в кадре сейчас она (перебивка),
// иначе 'cam1' — как было. Камера 2 неактивна -> всегда камера 1 (прежнее поведение).
// Явная цель ('cam1'/'cam2') от кнопки у поля своей камеры перекрывает видимую.
function isCam2Active(st){
  if(!st)return false;
  return (st.cam2_zoom&&st.cam2_zoom!=='none')
    || (st.cam2_fit!=null&&st.cam2_fit!==100)
    || (st.cam2_pan_x!=null&&st.cam2_pan_x!==0)
    || (st.cam2_pan_y!=null&&st.cam2_pan_y!==0)
    || (st.cam2_rot!=null&&st.cam2_rot!==0)
    || (st.cam2_zoom_cx!=null&&st.cam2_zoom_cx!==0.5)
    || (st.cam2_zoom_cy!=null&&st.cam2_zoom_cy!==0.5);
}
function zoomPickTarget(which){
  if(which==='cam1'||which==='cam2')return which;
  const vis=(typeof IPV!=='undefined'&&IPV&&IPV.curCi===1);
  return (vis&&isCam2Active(CURSTYLE))?'cam2':'cam1';}
// Ключи стиля точки наезда цели: единственное место, где имена камер расходятся.
function zoomPickKeys(target){
  return target==='cam2'?['cam2_zoom_cx','cam2_zoom_cy']:['cam1_zoom_cx','cam1_zoom_cy'];}
function pickZoomPoint(which){const st=$('ipvstage');
  if(ZOOM_PICK){zoomPickOff();return;}
  if(!st||!st.clientWidth){toast(t('Открой предпросмотр (шаг 3)'));return;}
  ZOOM_PICK=which||'auto';st.classList.add('zoompick');
  ['st_pickzoom','st_pickzoom2'].forEach(id=>{const b=$(id);if(b)b.textContent=t('Отменить точку');});
  zoomPickMark();}
function zoomPickOff(){ZOOM_PICK=false;
  const st=$('ipvstage');if(st)st.classList.remove('zoompick');
  ['st_pickzoom','st_pickzoom2'].forEach(id=>{const b=$(id);if(b)b.textContent=t('Прицел');});
  zoomPickMark();}
// ---- точка наезда: клик -> ИСХОДНИК кадра, точка исходника -> экран ----
// Клик приходит долей ЭКРАНА сцены, а в стиль обязан лечь ИСХОДНИК кадра: между ними
// стоит матрица кадра ipvCamMatrix (зум × заполнение, точка наезда, pan, слежение,
// поворот — всё, что рисует ipvCamPaint). Раньше записывалась доля экрана, и на
// увеличенном кадре прицел ставил точку не туда: у камеры 2 в «скачках» кадр увеличен
// почти всегда (104–137 %, в тейках больше 200 %), у камеры 1 в «наезде с откатом» он
// почти всё время 100 % — потому ошибка и была видна только на второй.
// Второй формулы не заводим: обратная матрица считается из ТОЙ ЖЕ ipvCamMatrix, что
// рисует кадр (образец — ipvCamChild/ipvRotoMaskZoom, тоже зовущие её, а не свою копию).
function zoomPickCam(target){return (target==='cam2')?'cam2':'cam1';}
// Точка ИСХОДНИКА (доли кадра) по доле ЭКРАНА сцены (ux, uy). Клик безразмерен, поэтому
// просто умножается на размер кадра: матрица живёт в px КОМПОЗИЦИИ (её и ставит
// ipvCamPaint через setTransform), а доля экрана от размера сцены не зависит.
function zoomPickSource(ux,uy,tm,target){
  const pl=(typeof IPV!=='undefined'&&IPV)?IPV.plan:null;
  const W=(pl&&pl.w)||1080,H=(pl&&pl.h)||1920;
  let x=ux*W,y=uy*H;
  const m=(typeof ipvCamMatrix==='function')?ipvCamMatrix(tm,zoomPickCam(target)):null;
  if(m){                                       // обратная матрица 2D: det = a*d − b*c
    const det=m[0]*m[3]-m[1]*m[2];
    if(det!==0){
      const dx=x-m[4],dy=y-m[5];
      x=(m[3]*dx-m[2]*dy)/det;y=(-m[1]*dx+m[0]*dy)/det;
      // Точка наезда в стиле — НЕ точка исходника, а точка КАДРА, где этот пиксель стоит без
      // зума: в AE это якорь нула, а горизонт поворачивает слой под нулом. Поэтому поворот
      // возвращаем обратно (R·p): при rot=0 это та же точка, при горизонте — без него
      // неподвижным при наезде оказался бы соседний пиксель.
      const s=Math.sqrt(det),co=m[0]/s,si=m[1]/s,rx=co*x-si*y,ry=si*x+co*y;
      x=rx;y=ry;
    }
  }
  return [x/W+0.5,y/H+0.5];                    // px композиции от центра -> доли кадра
}
function zoomPickMark(){const st=$('ipvstage');if(!st)return;
  let m=$('zoommark');
  // Маркер точки наезда — элемент ПРАВКИ: в режиме рендера его нет вовсе (кадр
  // снимается с этой же страницы, и маркер попал бы в готовый ролик). CSS его тоже
  // прячет (.render-mode .zoommark) — здесь он и не создаётся.
  if(!m&&typeof ipvRenderMode==='function'&&ipvRenderMode())return;
  if(!m){m=document.createElement('div');m.id='zoommark';m.className='zoommark';st.appendChild(m);}
  if(!ZOOM_PICK&&!ZOOM_HOVER){m.style.display='none';return;}   // вне правки точки маркер кадр не засоряет
  // Маркер — точка ТОЙ камеры, что правится сейчас (видимой или выбранной кнопкой).
  // Рисуется по ПРЯМОЙ матрице: перекрестие едет вместе с кадром, и на проигрывании
  // или перемотке оно остаётся ровно там, где точка исходника на экране СЕЙЧАС (зовёт
  // его ipvCamPaint на каждый нарисованный кадр), а не там, где точка в долях кадра.
  const tg=zoomPickTarget(ZOOM_PICK||ZOOM_HOVER_CAM);
  const kk=zoomPickKeys(tg);
  const cx=(CURSTYLE&&CURSTYLE[kk[0]]!=null)?CURSTYLE[kk[0]]:0.5;
  const cy=(CURSTYLE&&CURSTYLE[kk[1]]!=null)?CURSTYLE[kk[1]]:0.5;
  let ux=cx,uy=cy;
  if(typeof ipvCamMatrix==='function'){
    const pl=(typeof IPV!=='undefined'&&IPV)?IPV.plan:null;
    const W=(pl&&pl.w)||1080,H=(pl&&pl.h)||1920;
    const Wc=st.clientWidth||W,Hc=st.clientHeight||H;
    const m=ipvCamMatrix(ipvNow(),zoomPickCam(tg));
    // точка наезда (кадр без зума) -> точка исходника: снять горизонт (R⁻¹), см. zoomPickSource
    const qx=(cx-0.5)*W,qy=(cy-0.5)*H;
    const det=m[0]*m[3]-m[1]*m[2],s=Math.sqrt(det)||1,co=m[0]/s,si=m[1]/s;
    const px=co*qx+si*qy,py=-si*qx+co*qy;
    ux=(m[0]*px+m[2]*py+m[4])/W;
    uy=(m[1]*px+m[3]*py+m[5])/H;
  }
  m.style.display='';m.style.left=(ux*100)+'%';m.style.top=(uy*100)+'%';}
// Точка исходника для клика: обратный пересчёт и ограничение 0.02..0.98 — ОДНО место
// на весь прицел (его же проверяет стенд). Клик по сцене приходит долей экрана.
function zoomPickPoint(ux,uy,tm,target){
  const src=(typeof zoomPickSource==='function') ? zoomPickSource(ux,uy,tm,target) : [ux,uy];
  return [Math.max(0.02,Math.min(0.98,src[0])),Math.max(0.02,Math.min(0.98,src[1]))];}
function zoomPickClick(e){const st=$('ipvstage');if(!ZOOM_PICK||!st)return;
  const r=st.getBoundingClientRect();
  const ux=Math.max(0,Math.min(1,(e.clientX-r.left)/(r.width||1)));
  const uy=Math.max(0,Math.min(1,(e.clientY-r.top)/(r.height||1)));
  const tg=zoomPickTarget(ZOOM_PICK);                         // цель фиксируется на клике: кадр мог смениться
  // Экран -> исходник целевой камеры (обратная матрица), и только ПОСЛЕ него ограничение
  // 0.02..0.98: оно про точку исходника, а не про экран.
  const pt=zoomPickPoint(ux,uy,(typeof ipvNow==='function')?ipvNow():0,tg);
  const cx=pt[0],cy=pt[1];
  if(typeof applyZoomPoint==='function')applyZoomPoint(cx,cy,tg);
  else{
    if(!CURSTYLE)CURSTYLE=JSON.parse(JSON.stringify(STYLES.base||{}));
    const kk=zoomPickKeys(tg);
    CURSTYLE[kk[0]]=cx;CURSTYLE[kk[1]]=cy;
    zoomPickMark();
    if(typeof stRefresh==='function')stRefresh(kk[0]);
    else updateStyleDiffDots();
    if(typeof styleTouched==='function')styleTouched();
    captureAE();ipvPlanSoon();
  }
  zoomPickOff();}
document.addEventListener('pointerdown',e=>{if(ZOOM_PICK&&e.target&&e.target.closest('#ipvstage'))zoomPickClick(e);},true);
document.addEventListener('keydown',e=>{if(ZOOM_PICK&&e.key==='Escape')zoomPickOff();});
async function pickInto(id){try{const d=await (await fetch('/api/pickone')).json();if(d.path){$(id).value=d.path;stEdit();
  if(SFX_PREFIX[id])openSfxEdit(id);}}   // заменил звук — сразу настрой
  catch(e){toast(t('Не открылся выбор файла — сервер не ответил'));uiLog(t('pickone: ')+e);}}
// Сохранение шаблона: две кнопки — «Сохранить» (перезаписать выбранный шаблон без карандаша,
// имя уже известно) и «Сохранить как…» (завести новый). Для встроенных base/geologica
// «Сохранить» требует сохранить под своим именем.
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
// Выбор таблицы LUT (.cube) для камеры спикера — как pickdir, только файл и с
// фильтром .cube: таблицу кладут рядом с исходниками, и в общей папке её не найти.
async function pickcube(id){try{const d=await (await fetch('/api/pickcube')).json();if(d.path){$(id).value=d.path;}}
  catch(e){toast(t('Не открылся выбор файла — сервер не ответил'));uiLog(t('pickcube: ')+e);}}
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


// ---- редактор звука: волна, обрезка, точка удара ----
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
  if(typeof styleTouched==='function')styleTouched();
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
