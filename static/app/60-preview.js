// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// плеер предпросмотра, громкость, панель слов, разметка интро
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ===== один планировщик кадра на циклы превью: rAF + сторож-таймер =====
// Кадр игры редактора (edTick) и кадр предпросмотра вставок (ipvTick) планировались только
// через requestAnimationFrame. В скрытой вкладке браузер rAF не вызывает ВООБЩЕ, а <video>
// продолжает играть сам: перескок через вырезанное не делался ни разу, и в фоне слышно
// вырезанное (жалоба владельца — «слушаю фоном, играет всё подряд»).
// Полагаться на document.hidden нельзя: в живом браузере вкладка ушла в фон (и у перекрытого
// окна, и у встроенных панелей), rAF встал, а «скрыта» браузер не сказал — флаг остался false.
// Поэтому правильность держит СТОРОЖ: шаг планируется ещё и setTimeout'ом на
// PV_WATCHDOG_MS; пришёл rAF — сторож снимается и не шагает, не пришёл — шагает сторож.
// Плеер раскладки камер (cpvTick, 88-cams.js) сюда не входит: он и так подстрахован
// setInterval, и ломать его нечем.
const PV_FRAME_MS=20;      // период шага в скрытой вкладке, мс: перескок не позже пары кадров
const PV_WATCHDOG_MS=60;   // страховка на случай молчащего rAF, мс: шаг не реже ~60 мс
const PV_FRAMES=[];        // живые циклы кадра {P,fn,on}: их перевзводит смена видимости
let PV_FRAME_VIS=false;    // слушатель visibilitychange заведён ровно один
function pvFrameHidden(){return !!(typeof document!=='undefined'&&document.hidden);}
// Оба вида шага живут в РАЗНЫХ полях плеера: пауза обязана снять их разом, а не тот, о
// котором помнит вызывающий. После паузы не должен сработать ни rAF, ни таймер.
function pvFrameStop(P){
  if(P.raf){cancelAnimationFrame(P.raf);P.raf=0;}
  if(P.tim){clearTimeout(P.tim);P.tim=0;}}
// Следующий шаг цикла. Прежний взведённый шаг снимаем ПЕРВЫМ делом: два живых шага — это
// двойной перескок через вырезанное (шаг пришёл бы дважды на один кадр). Ровно один шаг
// держит токен P.step: сработавший колбэк снимает чужой (тот же токен) и не шагает.
// Токен ведёт САМ планировщик, а не поле вызывающего: `++P.step` на пересозданном плеере
// (IPV при открытии предпросмотра вставок пересобирается литералом, 85-inserts-view.js) дал бы
// NaN, а `NaN!==NaN` — всегда «шаг чужой»: ни rAF, ни сторож не шагнули бы, и цикл встал бы
// намертво с первого же открытия. Поля rAF и сторожа (`raf`, `tim`) этой болезнью не страдают:
// pvFrameStop читает их только на чтение (undefined — это «шага нет»), а заводит их сам
// планировщик присваиванием.
function pvFramePlan(P,fn){pvFrameStop(P);
  P.step=(P.step|0)+1;const step=P.step;   // `|0` гасит и undefined, и прежний NaN
  // В скрытой вкладке rAF не придёт никогда — не взводим его вовсе: шаг ведёт сторож.
  if(!pvFrameHidden())P.raf=requestAnimationFrame(()=>{
    if(P.step!==step)return;P.raf=0;if(P.tim){clearTimeout(P.tim);P.tim=0;}fn();});
  // Сторож нарочно НЕ снимает rAF: если шаг пришёл им, rAF уже снял сторожа — а снятие rAF
  // отсюда пустило бы лишний шаг по устаревшему колбэку там, где таймеры зовут пачкой.
  P.tim=setTimeout(()=>{if(P.step!==step)return;P.tim=0;fn();},PV_WATCHDOG_MS);}
// Цикл пошёл: помним его, чтобы пережить смену видимости, и планируем первый шаг.
function pvFrameStart(P,fn){
  const L=PV_FRAMES.find(x=>x.P===P);
  if(L){L.fn=fn;L.on=true;}else PV_FRAMES.push({P,fn,on:true});
  if(!PV_FRAME_VIS&&typeof document!=='undefined'&&document.addEventListener){
    PV_FRAME_VIS=true;document.addEventListener('visibilitychange',pvFrameReplan);}
  pvFramePlan(P,fn);}
// Цикл встал (пауза, конец клипа): снять оба вида шага и забыть цикл — иначе смена
// видимости воскресила бы остановленную игру.
function pvFrameOff(P){pvFrameStop(P);const L=PV_FRAMES.find(x=>x.P===P);if(L)L.on=false;}
// Переключили окно во время игры (alt-tab, другая вкладка): перевзвод снимает прежний шаг
// и планирует новый — ушли в фон, значит сторожевым таймером, вернулись, значит снова rAF.
function pvFrameReplan(){for(const L of PV_FRAMES){if(L.on)pvFramePlan(L.P,L.fn);}}

// ================= preview player (ported) =================
// Плеер шага 1 — РЕДАКТОР НАРЕЗКИ: играет его единственный <video> камеры 1
// (ED.play/ED.cs), отдельных монтажных кнопок нет вовсе. Здесь живёт общий объект
// кадра PV: камеры, дублёр, слова и EDL клипа. Своего состояния воспроизведения
// (PV.playing) у него больше нет — состояние ровно одно, редактора (ED.play).
// voicePanel — id панели «Голос» ЭТОГО плеера: по ней дорожка обработанного голоса
// (vt*, ниже) берёт живые ручки шумодава, а не профиль спикера. У плееров без панели
// (шаг 3) поля нет — им остаётся профиль.
// `silent` — все <video> этого кадра НЕМЫЕ всегда: звук шага 1 играет буфер Web Audio
// (блок `ea*` ниже), а не элементы. Писателей `muted` у <video> много (ракурс, гейт голоса,
// живой хост, пуск редактора), и каждый обязан добавить `||P.silent`: иначе любой из них
// вернул бы звук камеры поверх звука редактора — два голоса разом.
let PV={vids:[],bufs:[],cams:null,segs:[],audio:[],words:[],dur:0,aidx:0,vidx:-1,primed:-1,curCi:-1,rollCi:-1,scrubbing:false,scrubT:0,raf:0,xml:'',voicePanel:'pvvoice',silent:true};
// ===== двойной буфер камеры 1: склейка без замирания =====
// Раньше на КАЖДОМ стыке плеер делал av.currentTime=av.src прямо в момент склейки.
// Это честный seek-декодера (флаш буфера -> ближайший ключевой кадр -> декод вперёд):
// на 4K long-GOP 0.2-0.4 с замирания, причём вместе со звуком — звук идёт с этого же
// <video>. Видно было именно «в разрезе»: на смежных кусках seek не делается (порог
// 0.06) и там всё гладко.
// Лечим дублёром: второй <video> на том же файле заранее уводится на РАЗБЕГ перед
// нужным кадром, за PV_PREROLL до стыка пускается вживую (немой, невидимый), а на
// стыке элементы меняются местами — seek к этому моменту давно отыгран в фоне.
// Дублёр всегда лежит ВНЕ PV.vids, живой — всегда PV.vids[0]: весь остальной код
// (редактор, звук, camVisual, ED.cs) о подмене не знает.
const PV_PREROLL=0.35;   // сколько дублёр играет до стыка, прежде чем стать живым
const PV_SWAP_LO=-0.12;  // допуск позиции дублёра на стыке: не добежал (сек)
const PV_SWAP_HI=0.5;    // ...и перебежал; вне окна — откат на старый путь (seek на месте)
// Дорожка голоса проходит стык ТОЙ ЖЕ машиной и по тем же правилам: дублёр
// пускается за PV_PREROLL до стыка, а на стыке подменяется живым. Допуск позиции
// свой: <audio> не показывает кадр, и перебежавшая дорожка слышна как забежавший
// вперёд голос куда заметнее, чем лишний кадр картинки.
const VT_SWAP_LO=-0.06;  // допуск позиции дублёра дорожки голоса на стыке (сек)
const VT_SWAP_HI=0.25;
// ===== общая громкость видео-превью (монтаж / интро / раскладка камер) =====
// Одна настройка на все три плеера, живёт отдельным ключом localStorage (глобальная,
// как выбранный шаг). Ставим громкость ВСЕМ <video> плеера (слышен только не-
// заглушённый), чтобы при смене активной камеры уровень не прыгал. Новые <video>
// (см. openPreview/ipvRefresh/cpvOpen) берут MEDIA_VOL прямо при создании.
let MEDIA_VOL=1;
try{const _v=parseFloat(localStorage.getItem('reelsi_vol'));if(_v>=0&&_v<=1)MEDIA_VOL=_v;}catch(e){}
// ED — в списке обязательно: дорожка голоса шага 1 живёт на редакторе (vtOf(ED)), и без
// него ползунок не трогал обработанный голос — громкость оставалась той, что была при
// создании дорожки (жалоба «ползунок работает не всегда»: с обработкой — не работал).
function applyMediaVol(){[PV,(typeof ED!=='undefined'?ED:null),IPV,CPV].forEach(P=>{if(P&&P.vids)P.vids.forEach(v=>{if(v)v.volume=MEDIA_VOL;});
  if(P&&P.bufs)P.bufs.forEach(b=>{b.el.volume=MEDIA_VOL;});
  // Дорожка обработанного голоса — тем же множителем: ползунок громкости превью обязан
  // менять оба источника, иначе «стало» звучит громче «было» само по себе.
  if(P&&P.vt){if(P.vt.el)P.vt.el.volume=MEDIA_VOL;
    if(P.vt.vsp)P.vt.vsp.el.volume=MEDIA_VOL;}});
  // Музыка — тем же множителем, что голос. Иначе ползунок превью глушит ТОЛЬКО голос
  // (он идёт через <video>), музыка остаётся в полную, и баланс в превью врёт: юзер
  // компенсирует, ставит music_db −37, а в AE голос на полную — и музыки не слышно
  // (жалоба 2026-08-12). MEDIA_VOL — громкость прослушивания, она обязана менять
  // оба источника одинаково; в .jsx она не уезжает вообще.
  if(typeof MUSIC_EL!=='undefined'&&MUSIC_EL)MUSIC_EL.volume=MEDIA_VOL;
  if(typeof eaGain==='function')eaGain();               // звук редактора шага 1 — свой гейн
  if(typeof sfxSyncApply==='function')sfxSyncApply();}   // SFX-звуки тем же множителем
function setMediaVol(pct){MEDIA_VOL=Math.max(0,Math.min(1,(+pct||0)/100));
  try{localStorage.setItem('reelsi_vol',MEDIA_VOL);}catch(e){}
  applyMediaVol();syncVolUI();
  // Живой хост играет своим процессом, мимо страницы: громкость прослушивания
  // доходит до него этой командой (одна формула — 95-styles.js:voiceFxLiveOutDb),
  // иначе ползунок строки плеера глушил бы всё, кроме голоса из плагинов.
  if(typeof voiceFxLiveGain==='function')voiceFxLiveGain();}
function syncVolUI(){const p=Math.round(MEDIA_VOL*100);
  document.querySelectorAll('input[data-vol]').forEach(s=>{if(+s.value!==p)s.value=p;});}
// ===== стилевые громкости (музыка/голос, dB) — как в рендере =====
// Уровни MUSIC_DB/VOICE_DB из стиля звучат в превью сразу, чтобы не собирать проект
// ради «не громко ли». Один AudioContext на всё. Источник создаётся ОДИН раз на элемент
// и только при его создании — и ОБЯЗАТЕЛЬНО у дублёров (bufMake): плеер двухбуферный,
// на стыке дублёр меняется местами с живым <video>, и не повешенный источник после
// первого же стыка пустит звук мимо регулятора.
let AUDIO=null,VG=null,MG=null;
// Кто ЖДЁТ подключения к графу (voiceWiring) и граф ещё не разбужен. Это не лень ради
// лени: `createMediaElementSource` — дверь НЕОБРАТИМАЯ, после неё элемент отдаёт звук
// только в граф, а приостановленный AudioContext (браузер держит его в `suspended`,
// пока не было живого жеста) глушит этот путь целиком — без ошибки в консоли. Отсюда
// и дефект «видео играет, звука нет»: звук камеры заводили в граф БЕЗУСЛОВНО, ещё и
// на создании элемента, а будил его кто придётся. Теперь в граф уходит только то, что
// в нём нуждается: очередь разбирает ОДНА дверь — voiceEnsure (её зовут только те двери,
// где обработка реально звучит или громкость голоса не 0), а контекст будит ОДНА дверь —
// audioWake (её зовут все запуски плееров).
let VOICEPEND=[];
function audioGraph(){if(AUDIO)return;
  try{AUDIO=new (window.AudioContext||window.webkitAudioContext)();
    VG=AUDIO.createGain();MG=AUDIO.createGain();
    VG.connect(AUDIO.destination);MG.connect(AUDIO.destination);}
  catch(e){AUDIO=null;VG=MG=null;}
  applyDbGains();}
// ===== одна дверь на ВСЕ плееры: разбудить граф =====
// Зовётся из обработчика нажатия (браузер отпускает автозапуск только на живом жесте).
// Очередь на подключение она НЕ трогает: пустить элемент в граф — решение необратимое, и
// принимать его «раз уж всё равно будим» нельзя. Иначе первый же «Play» утаскивал бы в граф
// звук камеры без обработки и возвращал ровно тот дефект, от которого уходили.
// Раньше `AUDIO.resume()` был РОВНО один — в синхронизации МУЗЫКИ: плеер шага 3 звучал
// потому, что ipvPlay в конце звал musicSync, то есть граф будился побочно, через музыку,
// а плеер шага 1 не будил никто.
function audioWake(){
  audioGraph();
  if(AUDIO&&AUDIO.state==='suspended'&&AUDIO.resume)AUDIO.resume().catch(()=>{});}
// Подключить элемент к графу — единственное место, где зовётся createMediaElementSource.
function voiceGraphWire(v){
  if(!v||v.__wired)return false;
  audioGraph();if(!VG)return false;
  try{AUDIO.createMediaElementSource(v).connect(VG);v.__wired=true;return true;}catch(e){return false;}}
// Заявка на подключение: элемент создан, но нужен ли ему граф — решается позже
// (обработанная дорожка голоса, живой хост плагинов или громкость голоса стиля).
// Обычный звук камеры графа не касается вовсе и не зависит от состояния AudioContext.
// Граф уже нужен по громкости (voiceGraphNeeded) — элемент, созданный позже (звуковой
// прокси, дублёр стыка), подключается сразу: иначе «+3 дБ» стиля к нему не применятся.
function voiceWiring(v){if(!v||v.__wired)return;
  if(AUDIO&&voiceGraphNeeded()&&voiceGraphWire(v))return;
  // Метка «стоял в документе» — при постановке, а не при разборе: дублёр дорожки голоса
  // (vtSpareOf) в документ не вставляется никогда и должен подключаться как раньше.
  if(VOICEPEND.indexOf(v)<0){v.__inDom=!!v.isConnected;VOICEPEND.push(v);}}
// Правило «граф нужен по громкости»: громкость голоса стиля делает ТОЛЬКО VG.gain,
// `volume` элемента больше 1 не умеет. Одно место правила — его зовут voiceWiring и applyDbGains.
function voiceGraphNeeded(){
  const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
  return !!(s.voice_db!=null?s.voice_db:0);}
// Граф понадобился: подключить всё, что ждало, и разбудить контекст. Зовут те двери, где
// обработка РЕАЛЬНО звучит (vtGate при открытом гейте, vtEl/vtSpareOf при создании дорожки),
// и applyDbGains при громкости голоса не 0: её делает только VG.gain, `volume` элемента
// больше 1 не умеет. Второй двери подключения очереди быть не должно.
function voiceEnsure(){
  for(const v of VOICEPEND.splice(0)){   // копия: wire может добавить ещё
    // Страховка для элементов, удалённых из документа мимо mediaFree: стоявший в документе
    // при постановке и выпавший из него — мёртвый клип, в граф его не пускаем. Проверка
    // именно «был в документе», а не голое isConnected: дублёр дорожки голоса в документ не
    // вставляется никогда, и голое правило выбросило бы его из графа (громкость голоса мимо).
    if(v.__inDom&&!v.isConnected)continue;
    voiceGraphWire(v);}
  audioWake();}
function dbToGain(db){return Math.pow(10,(+db||0)/20);}
function applyDbGains(){if(!VG||!MG)return;const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
  VG.gain.value=dbToGain(s.voice_db!=null?s.voice_db:0);
  MG.gain.value=dbToGain(s.music_db!=null?s.music_db:-20);
  // При voice_db != 0 звук камеры обязан идти через граф — иначе «+3 дБ» в превью
  // не слышно. Дверь та же — voiceEnsure, правило — voiceGraphNeeded.
  if(voiceGraphNeeded()&&typeof voiceEnsure==='function')voiceEnsure();
  if(typeof eaGain==='function')eaGain();   // громкость голоса стиля — и звуку редактора шага 1
  // Живому хосту — итог этой громкости и громкости прослушивания (одна формула
  // живёт там же: 95-styles.js:voiceFxLiveOutDb).
  if(typeof voiceFxLiveGain==='function')voiceFxLiveGain();}
// Цензура: в рендере голос ныряет voice_db→−100 на окнах audio.censor из плана
// сцены. Окна считает scene_plan — здесь только «внутри окна или нет» и увод VG в ноль;
// вторую формулу не заводим. Вне окна возвращаем обычную громкость голоса из стиля.
function vgDuck(tm,plan){if(!VG)return;
  const cw=plan&&plan.audio&&plan.audio.censor;let mute=false;
  if(cw)for(const w of cw){if(tm>=w[0]&&tm<w[1]){mute=true;break;}}
  if(mute){VG.gain.value=0;return;}
  const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
  VG.gain.value=dbToGain(s.voice_db!=null?s.voice_db:0);}
// Заголовок шага 1 — имя ОТКРЫТОГО файла (как оно видно в списке клипов), а не название
// окна: у двух подряд открытых клипов заголовок был одинаковый и не говорил, что открыто.
// aria-label — то же имя (диалог называется тем, что в нём открыто); статический
// aria-label в разметке остаётся запасным — модалка всегда открывается отсюда.
function pvTitle(name){
  const ttl=$('mbPvTitle');if(!ttl)return;
  ttl.textContent=name||'';
  const dlg=ttl.closest('.modal');if(dlg&&name)dlg.setAttribute('aria-label',name);}
async function openEditClip(i){curEdit=i;const xml=CLIPS[i].xml;pvTitle(clipLabel(CLIPS[i]));
  if(curAE!==i||AEXML!==CLIPS[i].xml)selectAE(i);
  openModal('mbPreview');await openPreview(xml);await edOpen();
  // Панель «Голос» — ПОСЛЕ редактора, и это порядок, а не вкус: спикера она берёт по
  // ED.xml, а дорожку голоса заказывает по камере ЭТОГО плеера (vtPrep(ED)). Позванная
  // раньше (в openPreview до edOpen), она видела прежний клип и рисовала «У клипа нет
  // спикера», хотя тег у клипа есть, — и голос не запускался вовсе.
  await pvVoicePanel();}
let curEdit=-1;
// ===== превью-прокси камер =====
// Материал 4:2:2 10 бит (Sony/Canon) браузер НЕ берёт на аппаратный декодер:
// mediaCapabilities отвечает powerEfficient=false, и 4K жуётся софтом на проце —
// каждый seek на стыке сотни мс. Тот же материал в 720p 4:2:0 8 бит аппаратный.
// Прокси собирается ОТ ИСХОДНИКА, один раз на файл камеры, и правками нарезки не
// трогается (в отличие от черновика). Пока не готов — играем исходник, как раньше.
let PVPX={map:{},xml:'',poll:0,watch:[],height:0};   // watch — стойки плееров, ждущих прокси (см. pvProxyWatch)
function pvSrc(path){return '/api/media?path='+encodeURIComponent(PVPX.map[path]||path);}
// `extra` — файлы, которым прокси нужен не из-за камер: слой перехода видеовставки
// (Quick 2.mov — ProRes 4K, браузер его не декодирует вовсе). Собираются тем же
// сборщиком и той же дверью, что прокси камер: второй такой двери не заводится.
// `allintra` — all-intra разновидность прокси (ключевой кадр каждый): ею пользовался
// рендер без AE, пока камеры игрались прокси. Теперь рендер берёт кадры камер из
// исходников (core/webrender.py), и её никто не просит — параметр оставлен, потому что
// дверь /api/preview_proxy одна на всех, и её разновидность называет вызывающий.
async function pvProxyLoad(xml,build,extra,allintra){
  // height — КОРОТКАЯ сторона прокси. У живого превью это 720 (мельче не нужно: кадр
  // показывается в стойке шириной в треть экрана), у рендера без AE — короткая сторона
  // кадра ролика (1080 при 1080x1920): снимок идёт в натуральном размере, и 720-прокси
  // в нём был бы мылом. Ставит его вызывающий (см. templates/render.html).
  const height=PVPX.height||720;
  try{const d=await (await fetch('/api/preview_proxy',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({xml,build:!!build,height,extra:extra||[],allintra:!!allintra})})).json();
    if(d.error||!d.cams)return null;
    const m={};d.cams.forEach(c=>{if(c.ready&&c.proxy)m[c.path]=c.proxy;});
    (d.extra||[]).forEach(c=>{if(c.ready&&c.proxy)m[c.path]=c.proxy;});
    return {map:m,building:!!d.building,total:d.cams.length,ready:Object.keys(m).length};
  }catch(e){return null;}}
// Карта копится, а не заменяется: PV/IPV/CPV открываются на разные клипы, а ключ —
// абсолютный путь исходника, так что чужие записи только помогают.
function pvProxyMerge(px){if(px)Object.assign(PVPX.map,px.map);return px;}
// ===== Firefox: строка по факту, а не по догадке ==================================
// Firefox НЕ декодирует звук исходников камер: материал пишется `pcm_s16be` в MP4, и
// `<video>.mozHasAudio` честно отвечает `false` — в отличие от Chromium, который эту
// дорожку играет. Свойство есть ТОЛЬКО в Firefox, поэтому в Chromium строка не
// появляется никогда, а гадать по кодеку с сервера не нужно вовсе: спрашиваем элемент.
//
// Порог: столько камера обязана НАИГРАТЬ (currentTime продвинулся), прежде чем её
// ответу можно верить — до первого декодированного кадра `mozHasAudio` ещё не значит
// ничего. Строка эта НЕ про прокси, которого больше нет: она объясняет, почему звука
// нет у ИСХОДНИКА и что он появится, когда превью переедет на видео-прокси (в нём
// дорожка перекодирована вместе с картинкой).
const FIREFOX_FACT_MIN=1.5;
// Есть ли у <video> свойство только-Firefox. `in` — а не чтение: в Chromium свойства
// нет, и отличать «нет звука» от «нет свойства» обязательно.
function fxHasFact(v){return !!v&&('mozHasAudio' in v);}
// Читает ли Firefox звук ИСХОДНИКА этой камеры. true — да, false — нет,
// `null` — у этого элемента свойства нет вовсе (Chromium), `undefined` — свойство
// есть, но камера ещё не наиграла порог: судить рано. Ответ помним на элементе
// ВМЕСТЕ с его src: переезд на видео-прокси меняет src, и о молчании исходника
// говорить уже нечего — отсюда и `null` на новый источник. Разница «нет свойства» и
// «рано судить» тут существенна: на первом строка снимается, на втором — живёт.
function fxAudioFact(P){
  const h=(typeof vtMuteHost==='function')?vtMuteHost(P):P;
  const v=h&&h.vids&&h.vids[(h.audioCi)||0];
  if(!fxHasFact(v))return null;
  const src=String(v.src||v.currentSrc||'');
  let f=v.__fxFact;
  if(!f||f.src!==src)return undefined;   // источник сменился — прошлый ответ не про него
  if(f.on===null&&(+v.currentTime||0)>=FIREFOX_FACT_MIN){
    f.on=v.mozHasAudio!==false;return f.on;}
  return f.on===null?undefined:f.on;}
// Одна строка на ВСЕ плееры превью: ставится в том же контейнере, что и прогресс
// (`pvProgRow`), и уходит сама, когда причина исчезла. Заводится она ОТЛОЖЕННЫМ
// заходом (`FIREFOX_FACT_MIN` игры), а не в момент вызова: `mozHasAudio` до первого
// декодированного кадра ещё ничего не значит. Заход один: таймер живёт на элементе,
// а заводится заново только при СМЕНЕ его источника.
function pvAudioLimit(stage,P){
  if(!stage)return;
  const h=(typeof vtMuteHost==='function')?vtMuteHost(P):P;
  const v=h&&h.vids&&h.vids[(h.audioCi)||0];
  if(!v)return;
  const src=String(v.src||v.currentSrc||'');
  if(!fxHasFact(v)){   // свойства нет (Chromium): судить не по чему и ждать нечего
    _fxTimerReset(v,0);return;}
  const fact=fxAudioFact(P);
  if(v.__fxFact&&v.__fxFact.src!==src)   // источник сменился: прошлый ответ не про него
    pvProgDrop(stage,'fxaudio');
  if(fact===true){pvProgDrop(stage,'fxaudio');return;}   // свойство есть, звук читается
  if(fact===false){
    const el=pvProgRow(stage,'fxaudio','pvpx');
    if(!el.innerHTML)
      el.innerHTML='<div class="pvpx_line"><span class="pvpx_txt" data-fx_txt="1"></span></div>';
    const txt=el.querySelector('[data-fx_txt]');
    if(txt)txt.textContent=t('Firefox не читает звук этих камер — звук появится с прокси');
    return;}
  // Судить рано: ждём, пока элемент наиграет порог, — заходом на самом элементе.
  if(v.__fxFact&&v.__fxFact.src===src&&v.__fxTimer)return;
  v.__fxFact={src:src,on:null};
  _fxTimerReset(v,setTimeout(()=>{v.__fxTimer=0;
    pvAudioLimit(stage,(typeof vtMuteHost==='function')?vtMuteHost(P):P);},
    Math.ceil(FIREFOX_FACT_MIN*1000)));}
// Один таймер на элемент: смена источника снимает прежний заход, иначе он сработал бы
// по чужому ответу.
function _fxTimerReset(v,id){
  if(v.__fxTimer)clearTimeout(v.__fxTimer);
  v.__fxTimer=id;}
// Прогресс сборки — блоком ПОВЕРХ плеера. PXJOB на сервере один, поэтому
// блок рисует каждый плеер, который ждёт прокси (шаг 1 — монтаж, шаг 3 — вставки,
// раскладка камер): pvProxyWatch запоминает стойку, pvProxyPoll раздаёт ей свежие
// i/n/файл/процент. Пока сборка идёт — блок есть, кончилась — снимается.
//
// Блок — строка ОБЩЕГО контейнера прогресса стойки (`pvProgRow`): `vtVoiceLine` держит
// там же строку голоса. Идти разом могут оба, и тогда видно две строки друг над другом,
// а не два абсолютных блока у одного края, где второй накрывает первый.
function pvProxyBlock(stage,st){
  const pct=Math.max(0,Math.min(100,Math.round(+st.pct||0)));
  const el=pvProxyBox(stage);
  el.querySelector('.pvpx_fill').style.width=pct+'%';
  // Текст статуса берём из ОБЩЕГО словаря прогресса (55-progress.js): своей формулировки
  // у этого блока нет — иначе строка про прокси правку в одном месте не поймает.
  el.querySelector('.pvpx_txt').textContent=
    progStatusFor('proxy',{i:st.i||0,n:st.n||0,pct:pct});
  el.querySelector('.pvpx_hint').textContent=t('один раз на файл, дальше из кэша');}
// Строка прогресса в общем контейнере низа кадра (`.pvprog`). Контейнер один на стойку и
// живёт, только пока в нём есть строки: с двумя независимыми абсолютными блоками у
// `bottom:0` второй рисовался поверх первого. Порядок строк задаёт CSS (`order`), а не
// очерёдность создания: кто пришёл раньше, тому место не занимать. Поиск — по атрибутам,
// не по классам: так же ищет и мини-DOM стендов.
function pvProgRow(stage,kind,cls){
  let box=stage.querySelector('[data-pvprog]');
  if(!box){
    box=document.createElement('div');box.className='pvprog';
    box.setAttribute('data-pvprog','1');stage.appendChild(box);}
  let row=box.querySelector('[data-pvrow="'+kind+'"]');
  if(!row){
    row=document.createElement('div');row.className=cls;
    row.setAttribute('data-pvrow',kind);box.appendChild(row);}
  return row;}
// Снять строку; опустевший контейнер уходит следом — пустая подложка закрывала бы кадр.
function pvProgDrop(stage,kind){
  const box=stage&&stage.querySelector('[data-pvprog]');
  if(!box)return;
  const row=box.querySelector('[data-pvrow="'+kind+'"]');
  if(row)row.remove();
  if(!box.children.length)box.remove();}
// Разметка строки прокси — одно место: строка заводится по потребности и не
// пересоздаётся на каждый опрос (иначе полоса моргала бы).
function pvProxyBox(stage){
  const fresh=!stage.querySelector('[data-pvrow="proxy"]');
  const el=pvProgRow(stage,'proxy','pvpx');
  if(fresh)el.innerHTML='<div class="pvpx_bar"><span class="pvpx_fill"></span></div>'+
    '<div class="pvpx_line"><span class="pvpx_txt"></span><span class="pvpx_hint"></span></div>';
  return el;}
function pvProxyStages(st){const on=!!(st&&st.running);
  PVPX.watch=PVPX.watch.filter(id=>{
    const stage=$(id);if(!stage)return false;
    if(!on){pvProgDrop(stage,'proxy');return false;}
    pvProxyBlock(stage,st);return true;});}
function pvProxyWatch(stage){   // плеер ждёт прокси: следим за сборкой и показываем прогресс
  if(stage&&PVPX.watch.indexOf(stage)<0)PVPX.watch.push(stage);
  clearInterval(PVPX.poll);
  pvProxyPoll();                                 // статус сразу: иначе блок появится через 2 с
  PVPX.poll=setInterval(pvProxyPoll,2000);}
async function pvProxyPoll(){
  let s;try{s=await (await fetch('/api/preview_proxy_status')).json();}catch(e){return;}
  pvProxyStages(s);
  if(s.running)return;
  clearInterval(PVPX.poll);PVPX.poll=0;await pvProxyRefresh();}
function vLoaded(v){   // ждать метаданных после смены src (см. sparePrime/spareHandover)
  return new Promise(res=>{
    if(!v||v.readyState>=1)return res();
    const done=()=>{clearTimeout(T);v.removeEventListener('loadedmetadata',done);v.removeEventListener('error',done);res();};
    const T=setTimeout(done,3000);   // страховка: элемент мог быть выброшен сменой клипа
    v.addEventListener('loadedmetadata',done);v.addEventListener('error',done);});
}
function vSeeked(v){   // дождаться, когда элемент отыграл seek и декодировал кадр (переезд на паузе)
  return new Promise(res=>{
    if(!v||(!v.seeking&&v.readyState>=2))return res();
    const done=()=>{clearTimeout(T);v.removeEventListener('seeked',done);v.removeEventListener('canplay',done);v.removeEventListener('error',done);res();};
    const T=setTimeout(done,3000);   // страховка: прокси не открылся — просто не передаём эфир
    v.addEventListener('seeked',done);v.addEventListener('canplay',done);v.addEventListener('error',done);});
}
async function pvProxyRefresh(){   // прокси дособрались — обновить карту; живому <video> src не трогаем
  const px=await pvProxyLoad(PVPX.xml);if(!px)return;
  const was=JSON.stringify(PVPX.map);pvProxyMerge(px);
  if(JSON.stringify(PVPX.map)===was)return;
  // Слой перехода видеовставки живёт своим <video>: у него src ПЕРЕСТАВЛЯЕТСЯ (исходник
  // ProRes кадра не давал вовсе) — эта дверь есть только у превью вставок.
  if(typeof ipvTransRefresh==='function')ipvTransRefresh();
  // Переезд на прокси живому src не присваиваем: смена посреди игры сбрасывает элемент в
  // readyState 0 (чёрный кадр) и сдвигает время (баг). Стоящий плеер переезжает
  // дублёром сразу, играющий — на ближайшем стыке (sparePrime подтянет свежий src сам).
  // «Играет» спрашиваем дверью кадра (pvVidsPlaying), а не vtPlaying: у плеера шага 1
  // играет редактор, и через vtPlaying его живой <video> считался бы стоящим.
  for(const P of [PV,IPV,CPV])if(P&&P.vids&&P.vids.length&&!pvVidsPlaying(P))await spareHandover(P);
  // Прокси приехал (может, и видео): причина молчания Firefox ушла вместе с src —
  // строку об этом снимаем тем же опросом, что и прогресс.
  // Шаг 1 (pvstage) сюда не входит: его <video> немые, звук играет буфер редактора (`ea*`),
  // и молчание исходника в Firefox к нему отношения не имеет.
  for(const [id,P] of [['ipvstage',IPV],['cpvstage',CPV]]){
    const st=$(id);if(st&&typeof pvAudioLimit==='function')pvAudioLimit(st,P);}}
async function openPreview(xml){
  const stage=$('pvstage');
  const d=await (await fetch('/api/aicut_preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({xml})})).json();
  if(d.error){toast(errText(d));return;}
  clearInterval(PVPX.poll);PVPX.poll=0;PVPX.xml=xml;
  const px=await pvProxyLoad(xml,true);pvProxyMerge(px);
  // Правка нарезки режется по камере 1 — превью монтажа показывает только её: перебивки
  // вторых камер раскладываются позже и видны в предпросмотрах шага 2 (IPV/CPV), а тут
  // чужой исходник только сбивает (была жалоба «зачем в правках 2 камеры»).
  const cams=[d.cams[0]].filter(Boolean);PV.cams=cams;
  // Камеры клипа — и плееру шага 1 (редактор ED): дорожка обработанного голоса и живой
  // хост берут файл камеры 1 через vtCam1(P), а у ED своего /api/aicut_preview нет.
  ED.cams=cams;
  edPause();   // открылся другой клип: прежнее воспроизведение редактора стоп — его <video> сейчас пересоздаётся
  PV.xml=xml;PV.segs=(d.segs||[]).map(s=>({...s,ci:0}));PV.audio=d.audio&&d.audio.length?d.audio:d.segs;PV.words=d.words;PV.aidx=0;PV.vidx=-1;
  PV.dur=d.dur||(PV.audio.length?PV.audio[PV.audio.length-1].te:0);
  [...stage.querySelectorAll('video')].forEach(v=>mediaFree(v));
  PV.vids=cams.map((c,ix)=>{const v=document.createElement('video');
    v.src=pvSrc(c.path);v.preload='auto';v.muted=(ix!==0)||!!PV.silent;v.playsInline=true;
    v.volume=MEDIA_VOL;v.style.zIndex=(ix===0)?'2':'1';stage.insertBefore(v,$('pvsub'));return v;});
  PV.bufs=[];bufMake(PV,stage,$('pvsub'),0,0);
  PV.delta=camDeltas(PV);camBufs(PV,stage,$('pvsub'));   // тут камера одна, но контракт общий
  const camEl=$('edcam');   // «камера — склеек — длина»: строка переехала в редактор (блок «Монтаж» убран)
  if(camEl)camEl.textContent=cams.map((c,ix)=>(ix+1)+': '+(c.name||t('кам'))+(ix===0?t(' (звук)'):'')).join('  ·  ')+'  —  '+d.segs.length+t(' склеек · ')+Math.round(PV.dur)+t('с');
  // Панель «Голос» здесь НЕ рисуется: её зовёт openEditClip ПОСЛЕ edOpen (порядок см. там).
  if(px&&px.building)pvProxyWatch('pvstage');
  pvVideoTo(0);
  // Звук камеры 1 — в буфер редактора заранее, пока человек смотрит на клип: к «Играть»
  // он уже декодирован. Не ждём: картинке звук не нужен, а запрос — секунды.
  if(typeof eaOpen==='function')eaOpen(xml);
}
function pvSegAt(list,tm){for(let i=0;i<list.length;i++){if(tm<list[i].te-1e-3)return i;}return Math.max(0,list.length-1);}
// Слово под плейхедом: строка субтитра кадра. Ищется по МОНТАЖНОМУ времени (PV.words
// сняты с монтажа) — пересчёт из исходного времени редактора делает edWords (70-editor.js).
function pvWordAt(tm){for(const w of (PV.words||[])){if(tm>=w.s&&tm<w.e)return w.w;}return '';}
// Единственный плеер шага 1 — редактор: выставить его <video> на исходное время камеры 1
// и подвести звук. Дверей две, обе ведут сюда: edSeek (пауза, клик, хоткеи) и edTick (игра).
function pvVideoTo(src){const v=PV.vids&&PV.vids[0];if(!v)return;
  if(PV.audio&&PV.audio.length)PV.aidx=pvSegAt(PV.audio,Math.max(0,src));   // кусок EDL под этим местом
  PV.vidx=-1;   // перемотка: запрет «назад» в camApply снят
  try{v.currentTime=src;}catch(e){}
  spareIdle(PV);camIdle(PV);camApply(PV,src,false);}   // разбег не готовим: на паузе\протяжке это лишний seek на каждый кадр
// --- дублёр камеры 1: общая машина всех плееров ------------------------------------
// Машина дублёра — ОБЩАЯ на все плееры (объект кадра PV, редактор ED, вставки IPV, раскладка
// камер CPV): у всех цель одна и та же по смыслу — ИСХОДНОЕ время камеры 1. Поэтому она и
// хранится абсолютным исходным временем (b.at), а не индексом в списке сегментов; на этом
// же держится то, что редактор пользуется дублёром монтажного плеера, не заводя своего.
// P.bufs — дублёры, по одному на НЕПРЕРЫВНУЮ дорожку плеера. Слот 0 (ведущая камера, она
// же таймбаза) есть у всех. В раскладке камер добавляется второй — на звуковую камеру,
// когда звук слушают не с К1: её тоже гоняем через стык, только с постоянным оффсетом
// (CP.delta[k]) — поэтому у дублёра есть поле off.
function bufMake(P,stage,before,slot,off){
  const live=P.vids[slot];if(!live)return null;
  const el=document.createElement('video');
  // preload='metadata', а не 'auto': к дублёру обращаются ТОЛЬКО после явного seek на
  // разбег, качать \ демуксить начало файла второй раз — впустую удвоенный декод камеры
  el.src=live.src;el.preload='metadata';el.muted=true;el.playsInline=true;
  el.volume=MEDIA_VOL;el.style.zIndex='0';stage.insertBefore(el,before);   // дублёр — под всеми камерами
  if(!P.silent)voiceWiring(el);   // немому кадру (шаг 1) граф не нужен: звук у него свой
  const b={el,slot,off:off||0,at:null,rolling:false};(P.bufs=P.bufs||[]).push(b);return b;}
// `vsp` — дублёр дорожки голоса: живёт на другом элементе (<audio>, без слоёв и
// z-index), но правило у него ОБЩЕЕ: пока играет живой, дублёр стоит немым и с
// нормальной скоростью. Вторая копия гашения разошлась бы с первой.
function bufSilent(b){if(!b||!b.el)return;b.el.pause();b.el.muted=true;b.el.playbackRate=1;}
// Живой элемент дублёра: у камеры это её слот в P.vids, у дорожки голоса (слота нет) —
// её <audio> в st.el. Одна дверь на обмен ролями (bufSwap) и на его проверки.
function liveOf(P,b){return (b&&b.slot!=null)?P.vids[b.slot]:vtOf(P).el;}
function bufIdle(b){if(!b)return;b.at=null;b.rolling=false;
  if(typeof bufSilent==='function')bufSilent(b);   // стенды вырезают по функциям
  if(b.el.style)b.el.style.zIndex='0';}
function bufArm(P,b,at){   // увести дублёра на разбег перед кадром at
  if(!b||P.scrubbing||(b.at!=null&&Math.abs(b.at-at)<1e-3))return;
  b.at=at;b.rolling=false;try{b.el.currentTime=Math.max(0,at-PV_PREROLL);}catch(e){}}
function bufRoll(b,left){   // left — сколько осталось до стыка, сек
  if(!b||b.at==null||left>Math.min(PV_PREROLL,b.at))return;
  if(b.el.seeking||b.el.readyState<3)return;   // не долезли — не беда, отработает старый путь
  // Дублёр обязан прийти на стык КАДР В КАДР. Пустить его «как есть» мало: тик, на котором
  // решаем пускать, сам приходит с опозданием (в фоновой вкладке rAF молчит и остаётся
  // страховочный интервал 120мс), и дублёр не добегал ~0.15с — это уже за окном допуска
  // PV_SWAP_LO, подмена срывалась, стык шёл через seek. Он немой и невидимый, поэтому
  // недобег гасим СКОРОСТЬЮ: сколько медиа осталось пройти на сколько времени осталось.
  b.el.playbackRate=Math.max(0.25,Math.min(2.5,
    ((b.at-b.el.currentTime)/Math.max(0.05,left))*(b.rate?b.rate():1)));
  if(b.rolling)return;
  b.rolling=true;b.el.muted=true;b.el.play().catch(()=>{});}
// b.lo/b.hi — допуск позиции дублёра на стыке: у камер PV_SWAP_LO/HI, у дорожки
// голоса свой (VT_SWAP_LO/HI). <audio> кадра не показывает, и перебежавшая дорожка
// слышна как забежавший вперёд голос куда заметнее, чем лишний кадр картинки.
function bufTake(P,b,at){   // подменить живой элемент своего слота дублёром
  if(!b||!b.rolling||b.at==null||Math.abs(b.at-at)>1e-3)return false;
  const d=b.el.currentTime-at;
  const lo=(b.lo==null?PV_SWAP_LO:b.lo),hi=(b.hi==null?PV_SWAP_HI:b.hi);
  if(b.el.seeking||b.el.readyState<3||d<lo||d>hi){b.el.pause();b.rolling=false;return false;}
  return bufSwap(P,b);}
function bufSwap(P,b){   // обмен живой элемент ↔ дублёр: на стыке (bufTake) и на паузе (spareHandover)
  const old=liveOf(P,b);if(!old)return false;
  // Громкость прежнего живого: у дорожки голоса вышедший в эфир берёт ЕЁ, а не MEDIA_VOL
  // (см. ветку ниже). Забираем до смены ролей, потом элемента в той роли уже нет.
  const wasVol=(b.slot==null)?old.volume:null;
  if(b.slot==null)vtOf(P).el=b.el;   // дорожка голоса: живой — её <audio>
  else P.vids[b.slot]=b.el;          // камера: живой — слот в P.vids
  b.el=old;   // меняемся местами: прежний живой уходит в дублёры
  old.pause();old.muted=true;old.playbackRate=1;
  if(old.style)old.style.zIndex='0';               // ...и под все камеры
  // вышел в эфир — скорость строго 1: разгон нужен был только чтобы попасть на стык
  const live=liveOf(P,b);live.playbackRate=1;live.volume=MEDIA_VOL;
  // Дорожка голоса создаётся НЕМОЙ (разбег, vtSpareOf), и размьючивает её ровно эта дверь:
  // `muted` элемента глушит и путь через Web Audio (createMediaElementSource, voiceWiring),
  // поэтому без размьючивания после ПЕРВОГО стыка голос молчал до конца клипа — замер
  // архитектора: `vtOf(ED).el.muted===true` в 147 замерах из 147. Громкость — прежнего
  // живого: у голоса её двигает не только MEDIA_VOL (запасной уровень дорожки, 95-styles.js).
  // Видео-дублёров это не касается: там звук решает camVisual (`v.muted=(i!==ac)||…`).
  if(b.slot==null){live.muted=false;if(typeof wasVol==='number'&&isFinite(wasVol))live.volume=wasVol;}
  b.rolling=false;b.at=null;return true;}
function spareLead(P){return (P&&P.bufs&&P.bufs[0])||null;}   // дублёр ведущей камеры
// Обвязка под общий контракт плееров {audio:[{ts,te,src}], aidx}
function segGap(list,i){const a=list[i],b=list[i+1];   // сколько вырезано на стыке i->i+1, сек
  return (a&&b)?Math.abs(b.src-(a.src+(a.te-a.ts))):0;}
function spareIdle(P){(P.bufs||[]).forEach(bufIdle);}
function spareStop(P){(P.bufs||[]).forEach(b=>{b.el.pause();b.rolling=false;});
  vtSpareStop(P);}
function sparePrime(P){   // разбег к следующему стыку; свежий src дублёр подтягивает здесь
  const nx=P.audio[P.aidx+1];
  if(!nx||segGap(P.audio,P.aidx)<=0.06)return;   // смежные куски: подмена не нужна, живой доиграет
  // Переезд на прокси живому <video> src не меняет (сброс в readyState 0 = чёрный кадр, баг
  // BE). Дублёр под всеми камерами и нем — ему src меняем когда угодно: сравниваем с нужным
  // и при расхождении взводим заново только после загрузки метаданных. Не успел — bufRoll/
  // bufTake сами откажут по readyState<3, стык пройдёт старым путём, переезд повторится.
  (P.bufs||[]).forEach(b=>{
    const want=(P.cams&&P.cams[b.slot]&&P.cams[b.slot].path)?pvSrc(P.cams[b.slot].path):'';
    if(want&&b.el.src.split(location.origin).pop()!==want){
      b.el.src=want;b.at=null;b.rolling=false;   // смена src сбросила позицию — взводим заново
      vLoaded(b.el).then(()=>{if(P.audio[P.aidx+1]===nx)bufArm(P,b,nx.src+b.off);});
      return;
    }
    bufArm(P,b,nx.src+b.off);});
  // Дорожка голоса идёт через стык ТЕМ ЖЕ дублёром: источник у неё свой (запечённый
  // трек, а не файл камеры), поэтому «свежий src» подтягивает своя дверь.
  const vsp=vtOf(P).vsp;
  const fresh=!vsp||!vsp.armed;
  vtSpareArm(P);
  if(fresh)voicePrime(P);}
function spareRollAt(P,tm){const a=P.audio[P.aidx];if(!a)return;
  (P.bufs||[]).forEach(b=>bufRoll(b,a.te-tm));
  // Дорожка голоса идёт за временем ИСХОДНИКА (у редактора оно в P.cs, и к времени
  // монтажа из EDL не сводится) — поэтому левое время считается своим, уже
  // сосчитанным vtNow, а не вычитанием из tm.
  vtSpareRoll(P,vtNow(P));}
function spareSwap(P){const nx=P.audio[P.aidx+1];if(!nx)return false;   // возвращаем судьбу ВЕДУЩЕЙ
  let lead=false;
  (P.bufs||[]).forEach(b=>{const ok=bufTake(P,b,nx.src+b.off);if(b.slot===0)lead=ok;});
  vtSpareSwap(P);   // дорожка голоса проходит стык тем же дублёром
  return lead;}
// Дублёр дорожки голоса по свежему источнику живого <audio> (переезд на прокси).
// Живому src не меняют по той же причине, что и <video>: смена посреди игры сбрасывает
// элемент (провал в звуке). Дублёр нем — ему src менять когда угодно.
//
// Зовётся на СМЕНЕ источника, а не на каждом кадре: переезд — это сброс позиции
// дублёра, и повторять его каждый тик значило бы стирать уже отыгранный разбег (дублёр
// вечно стоял бы на at-PV_PREROLL).
function voicePrime(P){
  const st=vtOf(P),b=vtSpareOf(P),live=vtSpareLive(P);
  if(!b||!live)return;
  // Источник копируем у ЖИВОГО один в один, а не собираем из st.path: у запечённого
  // трека это '/api/media?path=…', и склейка своего пути разошлась бы с ним молча.
  // Расхождение снимает сам vtSpareOf (инвариант: дублёр играет файл живого трека).
  if(!live.src||b.el.src===live.src)return;
  b.el.src=live.src;b.at=null;b.rolling=false;b.armed=false;   // источник сменился — разбег взводим заново
  vLoaded(b.el).then(()=>{if(vtOf(P).vsp===b&&st.on)vtSpareArm(P);});
}
async function spareHandover(P){   // стоящий плеер: передача эфира дублёром, а не сменой src у живого
  // Судьбу живого <video> решает дверь кадра (pvVidsPlaying), а не поле плеера: у плеера
  // шага 1 играет редактор, и vtPlaying(PV) отдал бы «стоит» про играющий кадр.
  if(!P||pvVidsPlaying(P)||P.scrubbing||!(P.vids||[]).length)return;
  const b=spareLead(P);if(!b||!(P.cams&&P.cams[b.slot]&&P.cams[b.slot].path))return;
  const live=P.vids[b.slot];
  const want=pvSrc(P.cams[b.slot].path);
  if(!want||live.src.split(location.origin).pop()===want)return;   // живой уже на свежем источнике
  if(b.el.src.split(location.origin).pop()!==want){
    b.el.src=want;b.at=null;b.rolling=false;   // смена src сбросила позицию — взводим заново
    await vLoaded(b.el);
    if(pvVidsPlaying(P)||P.scrubbing||!P.vids[b.slot])return;   // пока грузили, плеер тронули
  }
  try{b.el.currentTime=live.currentTime;}catch(e){return;}   // подводим к кадру, что на экране
  await vSeeked(b.el);
  if(pvVidsPlaying(P)||P.scrubbing||!P.vids[b.slot]||b.el.seeking||b.el.readyState<2)return;
  bufSwap(P,b);   // кадр к подъёму уже декодирован — чёрного нет
  camVisual(P,P.curCi>=0?P.curCi:0,false);   // обмен сбросил z-index живого в 0 — слой и звук по ракурсу
}
// --- переключение ракурса: ОБЩАЯ машина всех плееров -------------------------------
// Раньше это были три почти одинаковые копии (pvApplyVisual / ipvApplyVisual / cpvApply),
// и каждая правка моргания вносилась трижды — с неизбежным расхождением.
// Контракт плеера: P.vids — камеры (0 — ведущая, она же таймбаза), P.segs — EDL
// {ts,te,src,ci}, P.audioCi — с какой камеры слушаем (по умолчанию 0), P.delta[k] — оффсет
// камеры k от ведущей, P.curCi/P.vidx/P.defAt — состояние показа.
//
// Главное решение (2026-08-08, третий заход): вторичная камера — НЕ «спящий кадр, который
// будят на стыке», а ВТОРАЯ НЕПРЕРЫВНАЯ ДОРОЖКА. Снято одним дублём, синхрон один на весь
// клип, значит её исходное время = исходное время ведущей + ПОСТОЯННЫЙ оффсет (P.delta[k],
// тот же расчёт, что у звуковой камеры в раскладке). Она играет всегда, стыки склейки
// проходит своим дублёром — и в момент смены ракурса ей не нужно ни просыпаться, ни
// доезжать: переключение = смена z-index, кадр уже нужный.
//
// Так закрывается то, ради чего было два предыдущих захода. Держать её на паузе с сидкой на
// стык (заход 2) значило: не успел декодер — показываем ПРЕЖНЮЮ камеру лишние кадры, и это
// ровно то моргание, на которое жаловались. Держать её играющей, но править дрейф сидкой
// (заход 1) — сидка сама роняла кадр и давала тот же откат ракурса. Теперь: играет всегда +
// дрейф гасится СКОРОСТЬЮ (±6% незаметно) + стык проходится подменой дублёра.
//
// Правила, каждое снято с живого бага:
//  1) камера, которая УЖЕ на экране, с экрана не уходит: дрейф её не снимает;
//  2) прежнюю камеру после стыка не задерживаем — только пока входящая физически seeking,
//     и не дольше CAM_DEFER (это запасной путь, а не нормальная работа);
//  3) скорость трогаем только у НЕМЫХ камер: у ведущей и звуковой с неё идёт звук.
const CAM_SOFT=0.04;   // попали: скорость обратно в 1
const CAM_HARD=0.5;    // скоростью не догнать — только seek
const CAM_RATE=0.06;   // насколько ускоряем/замедляем догоняющую камеру
// Ракурс переключается ПОРЯДКОМ СЛОЁВ, а не прозрачностью. Слой <video> с opacity:0
// композитор вправе не рисовать вовсе, и на возврате в 1 первый кадр приходит с
// запозданием — на экране в этот момент остаётся то, что было под ним, то есть прежняя
// камера. Кадры видео тут не при чём, поэтому в логах плеера этого и не видно.
// Камеры кроют кадр целиком (inset:0 + object-fit:cover + чёрный фон), значит верхняя
// просто закрывает остальные: слои всегда непрозрачны и всегда отрисованы, меняется
// только кто сверху. Дублёры лежат ниже всех (z=0) — они играют с опережением.
function camVisual(P,ci,play){const ac=P.audioCi||0;
  P.vids.forEach((v,i)=>{v.style.opacity='1';v.style.zIndex=(i===ci)?'2':'1';
    v.muted=(i!==ac)||!!P.voiceMute||!!P.liveMute||!!P.silent;
    if(play)v.play().catch(()=>{});});}   // играют ВСЕ камеры: см. «вторая непрерывная дорожка»
// Постоянный оффсет каждой камеры от ведущей — по первому её сегменту в EDL.
function camDeltas(P){const d=(P.vids||[]).map(()=>0);
  for(let k=1;k<(P.vids||[]).length;k++){const s=(P.segs||[]).find(g=>g.ci===k);if(!s)continue;
    const a=(P.audio||[])[pvSegAt(P.audio||[],s.ts)];if(!a)continue;
    d[k]=s.src-(a.src+(s.ts-a.ts));}
  return d;}
// Дублёр КАЖДОЙ используемой камере: на склейке она прыгает вместе с ведущей, и без дублёра
// это был бы seek — то самое замирание, только теперь ещё и на видимом ракурсе.
function camBufs(P,stage,before){
  for(let k=1;k<(P.vids||[]).length;k++){
    if(!(P.segs||[]).some(g=>g.ci===k))continue;
    bufMake(P,stage,before,k,(P.delta||[])[k]||0);}}
// время вторичной камеры: цель — исходное время ведущей + её оффсет.
function camTrack(P,v,want){
  const d=v.currentTime-want,ad=Math.abs(d);
  // Крупный разрыв (открылись, промотали, сорвалась подмена дублёра) скоростью не догнать —
  // seek, и ОДИН раз: пока декодер едет, повторный запрос каждый кадр сбрасывает готовый кадр.
  if(!vtPlaying(P)||v.paused||ad>CAM_HARD){v.playbackRate=1;
    if(ad>0.02&&!v.seeking){try{v.currentTime=want;}catch(e){}}return;}
  v.playbackRate=(ad<=CAM_SOFT)?1:(d<0?1+CAM_RATE:1-CAM_RATE);}
function camApply(P,tm,play){
  if(!P.segs||!P.segs.length)return;
  let vi=pvSegAt(P.segs,tm);
  // ЧАСЫ ПЛЕЕРА ДРОЖАТ НА СТЫКЕ — вот откуда бралось моргание, пережившее все правки показа.
  // Время монтажа считается от currentTime ВЕДУЩЕЙ камеры, а на склейке её подменяет дублёр,
  // которому позволено стоять в окне PV_SWAP_LO..HI (до 0.12с НЕдобега). Сразу после подмены
  // tm считается уже от него — и на кадр-другой откатывается ЗА границу куска. pvSegAt честно
  // возвращает предыдущий кусок, а в нём прежняя камера: показ прыгал «новая → прежняя →
  // новая». Видно это было только счётчиком: на ОДНОЙ смене «кам» набирал 4 вместо 1.
  // Во время игры по кускам назад не ходим. Назад — это перемотка, а она всегда идёт через
  // pvVideoTo, где P.vidx сбрасывается в -1 и запрет снимается.
  if(vtPlaying(P)&&P.vidx>=0&&vi<P.vidx){vi=P.vidx;if(P.stats)P.stats.back++;}
  const s=P.segs[vi];if(!s)return;P.vidx=vi;
  const ac=P.audioCi||0,lead=P.vids[0];
  // ВСЕ немые камеры ведём каждый кадр, а не только ту, что сейчас в эфире: входящая должна
  // быть готова ДО стыка, иначе её нечем показать и приходится тянуть прежнюю (то самое
  // моргание). Звуковую (ac) не трогаем — её ведёт свой синхронизатор, ей нельзя менять
  // скорость: с неё идёт звук.
  if(lead)for(let k=1;k<P.vids.length;k++){const v=P.vids[k];
    if(v&&k!==ac&&!v.error)camTrack(P,v,lead.currentTime+((P.delta||[])[k]||0));}
  // Показываем РОВНО ТО, что просит EDL, и ни кадром иначе. Никаких «подождём готовности,
  // а пока подержим прежний ракурс»: ждать = показывать ДРУГУЮ камеру, и это ровно та
  // жалоба, из-за которой переписывалось всё остальное («мелькает прежняя камера»).
  // Цикл показа идёт 60 раз в секунду, поэтому «подержим всего пару кадров» — это и есть
  // видимая вспышка чужого ракурса. Худшее, что может случиться теперь: камера не успела
  // отыграть seek и пару кадров стоит на своём прежнем кадре — тот же ракурс, замерший на
  // мгновение. Это несравнимо мягче и, после перевода камер в непрерывные дорожки, редко.
  const ci=s.ci;
  if(P.stats&&P.curCi>=0&&ci!==P.curCi)P.stats.cam++;   // первый показ — не смена ракурса
  if(P.stats){const v=P.vids[ci];if(v&&v.seeking)P.stats.stale++;}
  P.curCi=ci;
  camVisual(P,ci,play);}
function camIdle(P){P.defAt=0;                         // перемотка/пауза: скорости в норму
  (P.vids||[]).forEach(v=>{if(v)v.playbackRate=1;});}
// ---------------------------------------------------------------------------
// Общий шаг всех плееров: время монтажа + продвижение блока на стыке. Гонка была одной и
// той же в трёх копиях (pvTick/ipvStep/cpvStep) — чиним одну функцию. Гонка: сидка
// av.currentTime=a.src асинхронна, и пока она едет, currentTime всё ещё показывает ПРЕЖНИЙ
// кусок — время из него улетало далеко за a.te, условие конца срабатывало снова и блоки
// проскакивали пачкой («проигрывается вырезанное, не переключается на следующий блок»).
// Поэтому пока element.seeking, время из currentTime не считаем — держим плейхед на начале
// куска, и блок не продвигаем.
function pvStep(P){
  const av=P.vids[0];if(!av||!P.audio.length)return null;
  let a=P.audio[P.aidx],tm,adv=false,swap=false,seeked=false;
  if(av.seeking){tm=a.ts;}
  else{
    tm=a.ts+(av.currentTime-a.src);
    if(tm>=a.te-0.03){
      if(P.aidx>=P.audio.length-1)return {tm:0,end:true};
      adv=true;
      const done=spareSwap(P);P.aidx++;a=P.audio[P.aidx];
      // дублёр уже стоит на нужном кадре и играет — seek не нужен
      if(done)swap=true;else{const live=P.vids[0];
        // смежные в исходнике сегменты (сохранённый ✂ без удаления) — без seek: микро-seek залипает
        if(Math.abs(live.currentTime-a.src)>0.06){seeked=true;try{live.currentTime=a.src;}catch(e){}}}
      tm=a.ts;sparePrime(P);
    }
  }
  return {tm,a,adv,swap,seeked};}
// Своих часов воспроизведения у плеера шага 1 больше нет: играет ЕДИНСТВЕННЫЙ плеер —
// редактор, и его кадр (edTick, 70-editor.js) ведёт и картинку, и стыки pvStep, и звук
// vtTick. Монтажные pvPlay/pvPause/pvTick/pvScrub вместе с блоком «Монтаж» удалены:
// два плеера на одном <video> спорили за currentTime, и клик по таймлайну откатывался
// назад началом следующего куска монтажа.

// ===== Звук редактора шага 1: буфер Web Audio, часы — звук =====================
// Раньше звук шага 1 вели HTML-элементы: <video> камеры (без обработки) или <audio>
// запечённого голоса. На каждом стыке их перематывали или подменяли дублёром, а между
// стыками подгоняли к картинке СКОРОСТЬЮ ±6 %. Отсюда все жалобы разом: голос «плыл»
// (замер 2026-10-09: 63 смены скорости за 40 с), терял куски на промахе дублёра (5 из 16
// стыков) и молчал, пока картинка доезжала seek'ом.
//
// Теперь как в монтажке: ВЕДУЩИЙ — ЗВУК. Звук клипа целиком лежит в AudioBuffer (2–4 мин
// — десятки МБ), и на «Играть» каждый блок правки встаёт в очередь узлом
// AudioBufferSourceNode.start(когда, откуда, сколько) — с точностью до сэмпла, без
// перемотки и без подгонки скоростью. Очередь стоит заранее, поэтому порезанный звук
// играет верно и в фоновой вкладке, где кадры не приходят вовсе.
// Часы плеера — то, что СЕЙЧАС звучит (getOutputTimestamp), из них считается ED.cs, а
// немое видео догоняет звук (edFollow, 70-editor.js): скорость видео не слышна.
//
// Буферов два: звук камеры 1 (`raw`, /api/preview_audio — есть всегда) и обработанный
// голос (`proc`, его печёт и отдаёт панель «Голос» через vtUse). Играет обработанный, если
// он есть. Пока печётся новый, звучит прежний.
const EA_LEAD=0.03;   // через сколько после команды звучит первый сэмпл, с: в прошлое не ставим
const EA_FADE=0.004;  // склейка на стыке, с: без неё на обрыве волны слышен щелчок
let EA={xml:'',raw:null,rawUrl:'',rid:0,proc:null,procUrl:'',pid:0,
  nodes:[],segs:null,len:0,t0:0,clk:'',sig:'',gain:null};
// Клип открыт: звук камеры 1 — в буфер. Тот же клип (повторный openPreview после
// «Сохранить») буфер не перезагружает: камера та же.
async function eaOpen(xml){
  if(EA.xml===xml&&EA.rawUrl)return;
  eaStop();EA.xml=xml;EA.raw=null;EA.rawUrl='';EA.proc=null;EA.procUrl='';
  let d;
  try{d=await (await fetch('/api/preview_audio',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({xml})})).json();}
  catch(e){uiLog('preview_audio: '+e);return;}
  if(EA.xml!==xml)return;                 // пока вынимали, открыли другой клип
  if(d.error){toast(errText(d));return;}
  const url='/api/media?path='+encodeURIComponent(d.path);
  EA.rawUrl=url;
  const buf=await eaDecode(url);
  if(EA.xml!==xml||EA.rawUrl!==url||!buf)return;
  EA.raw=buf;EA.rid++;}
async function eaDecode(url){
  audioGraph();if(!AUDIO)return null;
  try{const ab=await (await fetch(url)).arrayBuffer();
    return await AUDIO.decodeAudioData(ab);}
  catch(e){uiLog('звук редактора не декодировался: '+e);return null;}}
// Обработанный голос готов (vtUse) или снят (vtDetach — null). Прежний буфер звучит, пока
// новый декодируется: подмена — одним присваиванием, очередь пересоберёт eaSync.
async function eaVoice(path){
  if(!path){EA.proc=null;EA.procUrl='';EA.pid++;return;}
  const url='/api/media?path='+encodeURIComponent(path);
  if(EA.procUrl===url)return;
  EA.procUrl=url;
  const buf=await eaDecode(url);
  if(EA.procUrl!==url||!buf)return;
  EA.proc=buf;EA.pid++;}
function eaBuf(){return EA.proc||EA.raw;}
// Подпись того, по чему стоит очередь: буфер, режим «слушать вырезанное», блоки правки.
// Сверяется на каждом кадре (eaSync) — так очередь пересобирают ВСЕ правки разом (тяга
// края, ✂, удаление, Ctrl+Z, возврат щели, вырез вздоха), а не каждая своей дверью.
function eaSig(){
  const b=EA.proc?'p'+EA.pid:(EA.raw?'r'+EA.rid:'n');
  return b+'|'+(ED.raw?1:0)+'|'+ED.blocks.map(x=>x.s0+','+x.s1).join(';');}
// Что звучит от места cs до конца: в обычном режиме — оставленные блоки, в режиме
// «слушать вырезанное» — исходник подряд. Блоки встык (✂ без удаления) сливаются: склейка
// посреди сплошного звука дала бы провал на 8 мс.
function eaPlan(cs){
  if(ED.raw)return cs<ED.dur?[{s0:cs,s1:ED.dur}]:[];
  const out=[];
  for(const b of ED.blocks){
    if(b.s1<=cs+1e-4)continue;
    const s0=Math.max(b.s0,cs),last=out[out.length-1];
    if(last&&s0-last.s1<1e-3)last.s1=b.s1;else out.push({s0,s1:b.s1});}
  return out;}
// Время, которое СЕЙЧАС звучит, по часам контекста. getOutputTimestamp — уже с задержкой
// вывода; нет его — вычитаем outputLatency. Контекст ещё спит (не было жеста) — часы
// страницы: картинка идёт, а звук встанет в очередь, как только контекст проснётся.
function eaNow(){
  if(EA.clk==='ctx'&&AUDIO){
    const ts=AUDIO.getOutputTimestamp?AUDIO.getOutputTimestamp():null;
    if(ts&&ts.contextTime>0&&ts.performanceTime>0)
      return ts.contextTime+(performance.now()-ts.performanceTime)/1000;
    return AUDIO.currentTime-(AUDIO.outputLatency||AUDIO.baseLatency||0);}
  return performance.now()/1000;}
function eaStop(){
  for(const n of EA.nodes){try{n.stop();}catch(e){}try{n.disconnect();}catch(e){}}
  EA.nodes=[];EA.segs=null;}
// Поставить очередь от места cs. Отрезки — во времени исходника, `m` — их смещение в
// монтаже от начала очереди.
function eaStart(cs){
  eaStop();audioGraph();eaGain();
  const ctx=!!(AUDIO&&AUDIO.state==='running');
  EA.clk=ctx?'ctx':'perf';
  EA.t0=(ctx?AUDIO.currentTime:performance.now()/1000)+EA_LEAD;
  let m=0;
  EA.segs=eaPlan(cs).map(p=>{const s={s0:p.s0,s1:p.s1,m};m+=p.s1-p.s0;return s;});
  EA.len=m;EA.sig=eaSig();
  const buf=eaBuf();
  if(!ctx||!buf||!EA.gain)return;
  for(const s of EA.segs){
    const dur=Math.min(s.s1,buf.duration)-s.s0;if(dur<=0)continue;
    const at=EA.t0+s.m,f=Math.min(EA_FADE,dur/4);
    const n=AUDIO.createBufferSource(),g=AUDIO.createGain();
    n.buffer=buf;n.connect(g);g.connect(EA.gain);
    g.gain.setValueAtTime(0,at);g.gain.linearRampToValueAtTime(1,at+f);
    g.gain.setValueAtTime(1,at+dur-f);g.gain.linearRampToValueAtTime(0,at+dur);
    n.start(at,s.s0,dur);EA.nodes.push(n);}}
// Где плейхед по звуку: {cs, end}. До первого сэмпла держим начало очереди.
function eaClock(){
  if(!EA.segs)return null;
  const el=eaNow()-EA.t0,S=EA.segs;
  if(!S.length)return {cs:ED.cs,end:true};
  if(el<0)return {cs:S[0].s0,end:false};
  for(const s of S){if(el<s.m+(s.s1-s.s0))return {cs:s.s0+(el-s.m),end:false};}
  return {cs:S[S.length-1].s1,end:true};}
// Кадр игры: очередь стоит по актуальным блокам и буферу, иначе — пересобрать от того
// места, что звучит сейчас. Проснувшийся контекст (был жест) — тоже пересборка: до него
// очередь шла по часам страницы и без звука.
function eaSync(){
  if(!EA.segs)eaStart(ED.cs);
  else if(EA.sig!==eaSig()||(EA.clk==='perf'&&AUDIO&&AUDIO.state==='running')){
    const c=eaClock();eaStart(c?c.cs:ED.cs);}
  eaGain();
  return eaClock();}
// Громкость звука редактора: громкость прослушивания (ползунок) × громкость голоса стиля.
// Свой гейн прямо в выход, а не через VG: VG уводит в ноль цензура шага 3 (vgDuck), и
// оставленный там ноль глушил бы шаг 1. Окно плагина открыто — звучит живой хост, свой
// звук в ноль (иначе голос слышен дважды).
function eaGain(){
  if(!AUDIO)return;
  if(!EA.gain){EA.gain=AUDIO.createGain();EA.gain.connect(AUDIO.destination);}
  const s=(typeof CURSTYLE!=='undefined'&&CURSTYLE)?CURSTYLE:{};
  const live=typeof vtLiveOn==='function'&&typeof ED!=='undefined'&&vtLiveOn(ED);
  EA.gain.gain.value=live?0:MEDIA_VOL*dbToGain(s.voice_db!=null?s.voice_db:0);}

// ===== Обработанный голос клипа: одна дорожка на ВСЕ превью =====
// Включён ИИ-шумодав (или плагин цепочки) — панель «Голос» считает ОБРАБОТАННЫЙ голос
// ВСЕГО клипа (серверный роут /api/voicefx_bake, кеш по содержимому настроек и
// исходника), и превью играет его вместо звука камеры. Трека два не бывает: тот же
// голос уезжает в AE, DRP, Premiere XML и черновой рендер — «один механизм» и есть
// этот файл. Плеер играет КОПИЮ из кеша (неизменяемую), а рядом с XML лежит файл
// версии текущих настроек — его читают читатели вывода.
//
// Код дорожки — ОБЩИЙ: функции `vt*` принимают плеер первым аргументом, а состояние
// живёт на самом плеере (P.vt). Им пользуется единственный плеер шага 1 (редактор ED) и
// превью шага 3 (IPV), а копии этого блока в 85-inserts-view.js нет и быть не должно:
// разъехавшиеся копии — это ровно то, из-за чего звук одного превью перестаёт совпадать
// со звуком другого.
//
// ВИДЕО — ХОЗЯИН, ЗВУК — ПОДЧИНЁННЫЙ. Этот блок НЕ трогает <video> вообще: ни play,
// ни pause, ни currentTime. Раньше было наоборот — окно звука в 20 с запрашивалось
// заново у края окна, на каждый такой запрос сервер считал шумодав заново, и видео
// вставало и прыгало («играет, потом стоит, звук идёт дальше»). Теперь звук
// подводится к позиции видео (vtSrcAt — время монтажа) и играет/стоит вместе с ним:
// один seek <audio> на расхождение больше допуска, и ничего больше.
//
// Пока трек считается, играет ПРЕДЫДУЩАЯ запечённая дорожка (а на первом заходе —
// исходный звук камеры), а строкой поверх кадра видно «голос пересчитывается…» /
// «голос обрабатывается, k %» (проценты — из хода самого шумодава: RoFormer печатает
// `N/M`). Готов — подмена источника <audio> без остановки видео.
//
// ЖИВЫЕ ПЛАГИНЫ — ТОЛЬКО ПРИ ОТКРЫТОМ ОКНЕ. Пока открыто окно плагина, звук голоса идёт
// через цепочку вживую: играет отдельный процесс хоста, а дорожка подключает голос
// ПОСЛЕ шумодава и ДО плагинов (запеки их сюда — цепочка слышалась бы дважды). Окна
// закрыты — хост не нужен вовсе, и все шаги просят ИТОГОВЫЙ голос (`final: true`) из
// кеша. Раньше хост играл всегда, и превью лагало: его не подогнать точнее 0,4 с.
//
// ЗВУК ДРУГОЙ КАМЕРЫ дорожка не подменяет: обработан голос камеры 1, и когда слушают
// камеру 2 (раскладка камер, cpvAudio), звучит она сама. Так же это решал голосовой
// прокси: он подменял звук ТОЛЬКО у камеры 1, остальные камеры шли обычным прокси со
// своим звуком. Гейт — по активной камере ЗВУКА (P.audioCi), а не по номеру элемента.
//
// Пороги синхрона. Маленькое расхождение гасится СКОРОСТЬЮ, как у камер (CAM_RATE):
// перемотка — это провал в звуке и щелчок, а ±6 % ускорения на слух незаметны, так
// что «концы не доигрывал» и «играл не там» из перемотки на каждый чих не берутся.
// Перемотка остаётся только на то, что скоростью не догнать: стык, прыжок бегунка,
// вырез, смена клипа.
const VT_SOFT=0.03;   // расхождение звука с видео, с: больше — подводим СКОРОСТЬЮ (±6 %)
const VT_DRIFT=0.25;  // больше — скоростью не догнать, только перемотка
const VT_RATE=0.06;   // насколько ускоряем/замедляем догоняющую дорожку
const VT_QUIET=400;   // затишье после правки ручки, мс: ползунок сыплется на каждый пиксель
const VT_POLL=1000;   // опрос хода запекания, мс: у RoFormer шаг — кусок клипа
// Файл камеры 1 открытого клипа: источник и для обработанного голоса, и для живого
// звука в окне плагина. Приезжает из /api/aicut_preview (P.cams) — второй копии
// «где взять камеру клипа» нет.
function vtCam1(P){return (P&&P.cams&&P.cams[0]&&P.cams[0].path)||'';}
// --- дублёр ДОРОЖКИ ГОЛОСА: та же машина, что у камер -------------------------------
// Видео проходит стык ДУБЛЁРОМ (bufArm/bufRoll/bufTake), а дорожка голоса прыгала на
// стыке перемоткой — и <audio> после seek начинает играть с задержкой 100–200 мс. На
// замере владельца это дало p95 120 мс при норме 22: картинка уже после стыка, а голос
// ещё доигрывает вырезанное. Лечим тем же приёмом и ТОЙ ЖЕ машиной: второй <audio> на
// том же файле заранее уводится на позицию после стыка и пускается немым, на стыке
// элементы меняются ролями — живого не сеcит никто.
//
// Машину не дублируем: `vsp` — такой же объект дублёра, что и `b` в P.bufs, и живёт он
// на bufArm/bufRoll/bufTake. Своё у него ровно то, чем <audio> отличается от <video>:
// допуск позиции на стыке (VT_SWAP_LO/HI), прицел (сегмент EDL, а у редактора — блок
// правки) и способ подмены (местами меняются два <audio>, а не слот в P.vids).
function vtSpareOf(P){   // дублёр дорожки голоса; живёт ровно столько, сколько трек
  const st=vtOf(P);
  if(!st.on||!st.el)return null;
  // Дублёр играет ТОТ ЖЕ файл, что живая дорожка. Трек сменил источник (открыли другой
  // клип, приехал новый запечённый файл) — прежний дублёр к новому стыку не относится, и
  // `armed` удержал бы взвод от повторного захода. Сброс — той же дверью, что и у смены
  // источника (vtSpareIdle), а сам источник берём у ЖИВОГО один в один, как voicePrime:
  // склейка своего пути из st.path разошлась бы с ним молча.
  if(st.vsp&&st.vsp.el.src!==st.el.src){
    vtSpareIdle(P);   // забыть разбег: источник этого дублёра — прошлый трек (см. vtSpareStop)
    st.vsp.el.src=st.el.src;
  }
  if(st.vsp)return st.vsp;
  // preload='metadata', а не 'auto': к дублёру обращаются ТОЛЬКО после явного seek на
  // разбег, качать и демуксить клип второй раз — впустую удвоенный декод голоса.
  const el=document.createElement('audio');
  el.preload='metadata';el.muted=true;el.volume=MEDIA_VOL;el.src=st.el.src;voiceWiring(el);
  // Дорожка голоса звучит только через граф (громкость стиля и цензура), и вот она-то
  // его и разбудит: это вторая дверь, где обработка РЕАЛЬНО нужна (первая — vtGate).
  if(typeof voiceEnsure==='function')voiceEnsure();
  // Роль живого держит liveOf: у дорожки голоса живой — это st.el, и на момент подмены
  // это уже ДРУГОЙ элемент, чем тот, что пришёл в подмену.
  const b={el,at:null,rolling:false,armed:false,off:0,lo:VT_SWAP_LO,hi:VT_SWAP_HI,
    rate:()=>{const v=P.vids&&P.vids[0];return (v&&+v.playbackRate)||1;}};
  st.vsp=b;return b;}
function vtSpareLive(P){const b=vtOf(P).vsp;return b?b.el:null;}
// Гасим дублёра, но НЕ забываем разбег: позиция уже отыграна в фоне, и взводить её
// заново на каждый кадр значило бы вернуть тот самый seek, от которого уходим.
// (vtSpareIdle — про другое: там дублёр действительно выбрасывается.)
function vtSpareStop(P){const st=vtOf(P),b=st.vsp;
  if(b&&typeof bufSilent==='function')bufSilent(b);}
// Забыть разбег: элемент сменил источник (другой трек). Позиция такого дублёра к цели
// не относится, а `armed` удержал бы взвод от повторного захода.
function vtSpareIdle(P){const st=vtOf(P),b=st.vsp;
  if(b&&typeof bufIdle==='function')bufIdle(b);
  if(b)b.armed=false;}
// Цель дублёра голоса — по сегментам EDL: сколько вырезано на стыке и куда переезжает
// дорожка. Смежные куски исходника (сохранённый ✂ без удаления) подмены не требуют:
// живой доиграет сам, как у камер (тот же порог 0.06).
function vtSpareSeg(P){const a=P.audio&&P.audio[P.aidx],nx=P.audio&&P.audio[P.aidx+1];
  if(!a||!nx||segGap(P.audio,P.aidx)<=0.06)return null;
  return {at:nx.src};}
// Прицел дорожки. По умолчанию (превью шага 3 и раскладка камер) он и есть конец
// текущего куска EDL. У редактора время исходника, и по EDL стык не находится: там
// блоки правки, время монтажа из них не выводится. Прицел ставит edArm (70-editor.js),
// чтобы второго правила «где конец куска» не завелось.
function vtSpareAt(P,at){vtOf(P).vspAt=at;}
// Взвести разбег и дать команду на переезд: одно место на оба прицела.
function vtSpareArm(P){
  const st=vtOf(P),at=st.vspAt;
  if(at==null||!st.on||!st.el)return;
  const b=vtSpareOf(P);   // трек мог подключиться уже ПОСЛЕ того, как прицел поставили
  if(!b)return;
  if(b.armed&&b.at!=null&&Math.abs(b.at-at)<1e-3)return;   // уже взведён на этот стык
  const fresh=!b.armed;
  b.armed=true;bufArm(P,b,at);
  if(fresh&&typeof voicePrime==='function')voicePrime(P);}
// Сколько медиа осталось дублёру до стыка. У камер левое время считается вычитанием
// из времени монтажа, здесь же время ИСХОДНИКА: у редактора оно в P.cs, и время
// монтажа из EDL к нему не сводится. Формулу не повторяем — берём у vtNow.
function vtSpareRoll(P,tm){
  const st=vtOf(P),b=st.vsp,at=st.vspAt;
  if(!b||at==null)return;
  bufRoll(b,Math.max(0,at-Math.max(0,+tm||0)));}
// Подмена на стыке. Дублёр не готов (readyState, позиция вне допуска) — false: стык
// пройдёт прежним путём, перемоткой живого. Это и есть запасной путь, и он остался.
function vtSpareTake(P,at){
  const st=vtOf(P),b=st.vsp;
  if(!b||!b.armed||at==null)return false;
  // Вторая защита того же инварианта: дублёр обязан играть ТОТ ЖЕ файл, что живая дорожка.
  // Источник сверяем ЗДЕСЬ, а не только при взводе: между взводом и стыком трек мог
  // смениться, и подмена выпустила бы в эфир голос ПРЕДЫДУЩЕГО клипа — ровно тот баг, от
  // которого уходим. Не тот файл — подмена не состоялась, стык пройдёт запасным путём.
  if(b.el.src!==st.el.src){vtSpareIdle(P);return false;}
  // Скорость в норму ПЕРЕД проверкой: позицию сверяем с той, что уже отыграна, иначе
  // подмена не случится ровно потому, что дублёр ехал ускоренно.
  b.el.playbackRate=1;
  if(!bufTake(P,b,at))return false;
  b.armed=false;   // разбег израсходован: прежний дублёр теперь ЖИВОЙ, а не разбег
  return true;}
function vtSpareSwap(P){   // стык из pvStep: прицел — сегмент EDL
  const seg=vtSpareSeg(P);if(!seg)return false;
  vtSpareAt(P,seg.at);return vtSpareTake(P,seg.at);}
// Кадр дорожки по дублёру — ОДНА дверь из vtTick (здесь же и проверка, что машина на
// месте: стенды вырезают из файла по функциям, и без неё вызов упал бы «не определено»).
function vtSpareCtl(P,at,tm){
  if(typeof vtSpareArm!=='function'||typeof vtSpareTake!=='function')return;
  const vsp=vtOf(P).vsp;
  vtSpareArm(P);
  if(!vsp&&typeof voicePrime==='function')voicePrime(P);   // трек подключился уже во время игры — источник догоняем сами
  const cue=vtOf(P).vspAt;
  // Прицел у плеера по EDL и есть точка стыка: в окне VT_SWAP_HI подменяем в этом же кадре.
  // (Редактор шага 1 сюда не заходит: его звук — буфер Web Audio, блок `ea*`.)
  if(cue!=null&&Math.abs(cue-at)<=VT_SWAP_HI)vtSpareTake(P,cue);
  vtSpareRoll(P,tm);}

// Состояние дорожки — на плеере: у шага 1 и шага 3 свои элементы, свои запросы и своя
// очередь, а код один. Поля: `timer` — затишье после правки ручки (pvVoiceTune),
// `poll` — опрос хода запекания. Таймеры РАЗНЫЕ нарочно: общий поле `timer` затирало бы
// то опрос хода, то отложенный пересчёт, и смена ручки терялась бы в середине счёта.
// `vtq` — «стою в очереди», `vtend` — строку хода снял законный конец.
function vtOf(P){
  // `dn` — настройки шумодава, под которые в <audio> стоит дорожка; `wantDn` — под
  // которые её сейчас просим. Плагины в них не входят нарочно: дорожка шумодава от
  // цепочки не зависит, и правка плагина её не пересчитывает и не переподключает.
  // `fin` — какой голос стоит в `path`: итоговый (с плагинами) или дорожка шумодава.
  // Без него «плагины выключили» не отличить от «уже играем то же самое», и плеер
  // либо просил бы сервер на каждом кадре, либо остался бы на дорожке шумодава.
  // `statesWait` — кто ждёт событие `states` от живого хоста (см. `vtHostDown`).
  // `wantFinal` — итоговый голос просим (с плагинами) или дорожку шумодава (живому хосту).
  // `vsp` — дублёр дорожки голоса, `vspAt` — прицел разбега (см. vtSpareOf/vtSpareAt).
  if(!P.vt)P.vt={on:false,el:null,path:'',fin:false,seq:0,timer:0,poll:0,want:'',note:'',
    vtq:false,vtend:false,live:null,dn:'',wantDn:'',wantFinal:false,warn:'',statesWait:null,
    vsp:null,vspAt:null};
  return P.vt;}
// Исходное время камеры 1 под бегунком: кусок монтажа (P.audio[P.aidx]) плюс
// смещение ВНУТРИ него. Именно так, а не «разница с началом клипа»: на стыке
// начинается ДРУГОЙ кусок, и своё исходное время считается от его начала. Одна
// формула на всё, что просит звук с сервера: обработанный голос и окно плагина.
//
// Плеер шага 1 (редактор) — исключение, и только он: его <video> играет ИСХОДНИК, и
// исходное время камеры 1 (ED.cs) уже лежит на самом плеере. Пересчитывать его из
// монтажного таймлайна значило бы завести вторую копию правила «где плейхед».
function vtSrcAt(P,tm){
  if(typeof ED!=='undefined'&&P===ED)return Math.max(0,+P.cs||0);
  const a=(P.audio&&P.audio[P.aidx])||null;if(!a)return null;
  return a.src+Math.max(0,Math.min(tm-a.ts,a.te-a.ts));}
// Какая камера звучит в плеере: у раскладки камер это P.audioCi, у остальных камера 1.
function vtAudioCam(P){return (P&&P.audioCi)||0;}
// Идёт ли воспроизведение ЭТОГО плеера. Поле своё у каждого (IPV.playing, ED.play), и
// второй копии «кто играет» из общей дорожки не заводится: у шага 1 играет редактор.
function vtPlaying(P){
  if(typeof ED!=='undefined'&&P===ED)return !!P.play;
  return !!P.playing;}
// Играют ли <video> ЭТОГО плеера. Дверь НЕ равна vtPlaying(P), и разница — ровно на
// объекте кадра шага 1: плеер шага 1 один (редактор ED), а его окно с кадром (PV) играет
// через ED и «кто играет» про себя не знает — `PV.playing` (>02.10) не ставит никто.
// Спрашивать в таких местах vtPlaying(PV) значит услышать «стоит» про ИГРАЮЩИЙ кадр:
// переезд на прокси брал живой <video> из эфира прямо на ходу, дублёр вставал неиграющим
// и на экране застывал кадр при живой кнопке «пауза». Поэтому «кадр играет» — отдельный
// вопрос, и у PV на него отвечает редактор.
function pvVidsPlaying(P){
  if(typeof PV!=='undefined'&&P===PV)return !!P.playing||!!(typeof ED!=='undefined'&&ED&&ED.play);
  return vtPlaying(P);}
// Время монтажа плеера. У шага 3 своя дверь (ipvNow): в рендере без AE время —
// НОМЕР КАДРА, и общая формула «от currentTime ведущей» там соврала бы. У редактора
// время тоже своё (исходник, ED.cs) — им и меряем, а не currentTime <video>.
function vtNow(P){
  if(typeof IPV!=='undefined'&&P===IPV&&typeof ipvNow==='function')return ipvNow();
  if(typeof ED!=='undefined'&&P===ED)return Math.max(0,+P.cs||0);
  const a=(P.audio&&P.audio[P.aidx])||null;
  return (a&&P.vids&&P.vids.length)?a.ts+(P.vids[0].currentTime-a.src):0;}
// <audio> обработанного голоса: свой элемент, а не <video> — видео занято картинкой и
// своими стыками, а звуку нужен ровно запечённый трек. Через общий граф (voiceWiring):
// громкость голоса стиля и цензура действуют на него так же, как на звук камеры, иначе
// «стало» врало бы по уровню.
function vtEl(P){
  const st=vtOf(P);
  if(st.el)return st.el;
  const el=document.createElement('audio');el.preload='auto';
  voiceWiring(el);
  // Дорожка голоса звучит через граф (громкость стиля и цензура) — и она же его будит:
  // обработка включена, значит граф нужен. Без обработки сюда никто не заходит вовсе.
  if(typeof voiceEnsure==='function')voiceEnsure();
  st.el=el;return el;}
// Пока играет обработанный голос, звук <video> глушим — иначе слышно два голоса
// разом. Флагом на плеере, а не одной установкой muted: <video> пересоздаётся и
// меняется местами с дублёром на стыке, и установка «один раз» до стыка не дожила бы
// (см. camVisual: он и есть одно место, где решается, кто звучит).
//
// Глушим, ПОКА ДОРОЖКА ПОДКЛЮЧЕНА, а не только пока обработанный звук реально играет.
// Было «live && el.readyState>=2», и на перемотке <audio> падал в readyState<2: гейт
// открывался, и в этот момент было слышно СЫРОЙ голос камеры — ровно то, на что
// жаловались («при перемотке ползунком звук пролагивает и включается старый»).
// Короткая тишина на догоне лучше старого голоса: камера молчит, пока трек наш.
//
// Дорожка камеры 1 звучит только тогда, когда звук идёт С камеры 1: слушают камеру 2 —
// её никто не глушит, а голос камеры 1 молчит (vtTick).
function vtGate(P,on){
  const ac=vtAudioCam(P),live=!!on&&ac===0&&!!vtOf(P).on,M=vtMuteHost(P);
  // Граф понадобился: голос реально звучит, значит звук камеры обязан идти через него
  // (громкость стиля и цензура). Здесь и только здесь камера попадает в граф:
  // обработки нет — графа нет, и простой звук камеры не зависит от AudioContext.
  if(live&&typeof voiceEnsure==='function')voiceEnsure();
  M.voiceMute=live;
  const v=M.vids&&M.vids[ac];if(v)v.muted=live||!!M.silent;}
// Надпись про голос — в панель «Голос» плеера (она есть у превью нарезки). Плееру без
// панели (шаг 3) писать некуда: там голос не настраивают, а только слушают.
function vtNote(P,text){
  if(!P.voicePanel||typeof voiceFxStatus!=='function')return;
  voiceFxStatus($(P.voicePanel),text);}
// Сбой запекания — ВИДИМЫЙ. Раньше причину знал только лог: на кадре стояла мелкая
// серая заметка, играл СЫРОЙ звук камеры, и «голос пропал» оставалось загадкой.
// Теперь строка голоса и надпись панели прямо говорят, что обработки нет и почему,
// а тост показывается ОДИН РАЗ на клип и настройки: иначе он всплывал бы на каждый
// опрос хода и на каждую перерисовку панели. Звук камеры как запасной остаётся.
function vtVoiceFail(P,why){
  const st=vtOf(P),text=t('⚠ голос без обработки: ')+(why||t('см. логи'));
  st.failed=true;
  vtVoiceLine(P,text);vtNote(P,text);
  if(st.warn!==st.want){st.warn=st.want;toast(text);}}
// Имя клипа для строки прогресса: то же, что в заголовке превью (openEditClip).
function vtName(P){
  const c=(P.xml&&typeof clipByXml==='function')?clipByXml(P.xml):null;
  return (c&&typeof clipLabel==='function')?clipLabel(c):t('голос клипа');}
// Настройки голоса спикера клипа — из профиля (SPEAKERS). Ими же собирается проект,
// поэтому превью шага 3 — у него панели нет — просит трек ровно под них.
function vtProfileFx(P){
  const c=(P.xml&&typeof clipByXml==='function')?clipByXml(P.xml):null;
  const key=clipSpeaker(c);
  const prof=(key&&typeof SPEAKERS!=='undefined'&&SPEAKERS[key])||null;
  return (prof&&prof.voice_fx)||{};}
// Под какие настройки просим трек. Плеер со своей панелью «Голос» (шаг 1) спрашивает
// РУЧКИ на экране: до «Сохранить у спикера» источник правды для него — они. У остальных
// панели нет, и берётся профиль спикера, то есть то, по чему соберётся проект.
function vtFx(P){
  const host=(P.voicePanel&&typeof $==='function')?$(P.voicePanel):null;
  if(host&&host.dataset&&host.dataset.vfxmode==='panel'&&typeof voiceFxRead==='function')
    return voiceFxRead(host);
  return vtProfileFx(P);}
// Отцепить звук: трек больше не наш (другой клип, выключенная обработка).
function vtDetach(P){
  const st=vtOf(P);st.on=false;st.path='';st.fin=false;st.dn='';
  // Дублёр говорил прежним треком — его разбег больше не наш.
  if(typeof vtSpareIdle==='function')vtSpareIdle(P);
  // Шаг 1: буфер обработанного голоса снимаем — звучит звук камеры 1 (eaBuf).
  if(vtIsEd(P)&&typeof eaVoice==='function')eaVoice(null);
  const el=st.el;
  if(!el)return;
  el.pause();
  try{el.removeAttribute('src');el.load();}catch(e){}}   // load() сбрасывает прежний трек
// Выключили обработку (или открыли другой клип): звук обратно камере, трек — в никуда.
// Запечённый файл клипа при этом убирает СЕРВЕР (clear_final_voice): «выключил шумодав —
// звук исходный» обязано работать и для XML, DRP и чернового рендера, которые читают его.
function vtStop(P){
  const st=vtOf(P);st.seq++;
  clearTimeout(st.timer);st.timer=0;
  clearTimeout(st.poll);st.poll=0;
  st.vtq=false;st.vtend=true;st.want='';
  vtLivePause(P);
  // Живой хост плагинов живёт вместе с превью ЭТОГО плеера: другой клип или закрытие —
  // процесс гасится (по PID, на сервере), и после превью не остаётся ни одного.
  if(typeof voiceFxHostStop==='function')voiceFxHostStop();
  vtDetach(P);vtVoiceLine(P,'');vtNote(P,'');}
// Живое окно плагина: пока оно открыто, звук голоса идёт через него — вровень с
// картинкой (см. `vtLive*` ниже). Звук камеры 2 живёт как обычно: окно обрабатывает
// ТОЛЬКО голос камеры 1, и глушить чужую камеру нечем.
function vtLiveOn(P){
  return (typeof voiceFxLiveOn==='function')&&!!voiceFxLiveOn();}
// Глушение звука камеры плеера: решается В ОДНОМ месте и для дорожки, и для живого
// окна. Иначе два писателя (vtTick и vtLiveUpdate) затирали бы друг другу флаг, и
// звук камеры то замолкал бы поверх плагина, то звучал вместе с ним.
function vtSetMute(P,on){
  const M=vtMuteHost(P);
  M.voiceMute=!!on;
  const ac=vtAudioCam(P),v=M.vids&&M.vids[ac];
  if(v)v.muted=!!M.voiceMute||!!M.liveMute||!!M.silent;
  return !!M.voiceMute;}
// Кадр плеера для ЖИВОГО окна: позиция — исходное время камеры 1 (та же формула,
// что у дорожки, vtSrcAt), пуск/пауза — состояние плеера, стык и перемотка —
// команда seek. Глушение своего голоса (дорожка vt и звук камеры 1) снимается и
// ставится ЗДЕСЬ: пока плагин звучит, свой голос молчит, иначе слышно два голоса.
// Окно закрылось — возвращаем звук дорожке (`voiceFxLiveOn` стал false).
const VT_LIVE_DRIFT=0.4;    // расхождение, после которого шлём перемотку, сек
const VT_LIVE_MS=250;       // как часто можно слать перемотку: ползунок сыплется
function vtLiveUpdate(P,tm){
  const st=vtOf(P),on=vtLiveOn(P),ac=vtAudioCam(P),M=vtMuteHost(P);
  if(!on){
    // Живого звука больше нет (окно закрыли): снимаем своё глушение и возвращаем
    // звук дорожке — иначе камера осталась бы немой до следующего кадра vtTick.
    if(st.live){st.live=null;}
    if(M.liveMute!==undefined){
      M.liveMute=undefined;M.voiceMute=false;
      const v=M.vids&&M.vids[ac];if(v)v.muted=!!M.silent;
    }
    return false;}
  if(!st.live)st.live={sid:'',at:-1,sent:0,pending:0,rate:1};
  if(ac!==0){
    // Слушают камеру 2: хост обрабатывает голос камеры 1, и звучать ему нечем — камера 2
    // играет сама, и глушить её нельзя (это её звук, а не голос спикера).
    if(!st.live.paused)vtLiveCmd(P,'pause');
    if(st.el&&!st.el.paused)st.el.pause();
    M.liveMute=false;
    return false;}
  const at=vtSrcAt(P,tm);
  if(at==null)return false;
  const want=Math.max(0,at),live=vtPlaying(P)&&!P.scrubbing;
  // Звук камеры 1 глушим, только если плагин реально играет: на паузе слышно, как
  // ручка меняет звук ровно на этом месте, — это и есть «крутишь и слышишь».
  M.liveMute=live;
  M.voiceMute=false;
  const v=M.vids&&M.vids[vtAudioCam(P)];
  if(v)v.muted=!!M.liveMute||!!M.silent;
  // Своя дорожка обработанного голоса молчит: звучит хост. Она остаётся ПОДКЛЮЧЁННОЙ
  // (`st.on`), а не отцепляется: хост гасят, когда плагины выключили, и тогда звук
  // должен вернуться ей без нового захода за дорожкой на сервер.
  if(st.el&&!st.el.paused){st.el.pause();}
  if(!live){vtLiveCmd(P,'pause');st.live.at=want;return true;}
  // Перемотку шлём не на каждый кадр: 60 команд в секунду в трубу — это и мусор в
  // логе, и дёрганый звук. Отдельно ловим СТЫК монтажа: там позиция прыгает сама,
  // а с ней и кусок исходника (иначе звук уехал бы на вырезанное место).
  // Сравниваем с тем, где звук окна ДОЛЖЕН быть сейчас (последняя команда + прошедшее
  // время), а не с местом последней команды: иначе при обычном проигрывании через
  // VT_LIVE_DRIFT секунд уходила бы перемотка, и так каждые 0,4 с — щелчок и
  // обрубленный хвост ревербератора (сброс плагинов на каждой перемотке).
  const far=st.live.at<0||Math.abs(want-vtLiveExpect(P,st))>vtLiveRate(P,st)*VT_LIVE_DRIFT;
  if(far)vtLiveCmd(P,'seek',want);
  else vtLiveCmd(P,'play',want);
  return true;}
// Где звук окна сейчас по нашим часам: место последней команды play/seek плюс время,
// прошедшее с неё (на паузе — стоит). Окно играет само, превью только задаёт место.
function vtLiveExpect(P,st){
  const l=st.live;if(!l||l.at<0)return -1;
  if(l.paused||!l.t0)return l.at;
  return l.at+(Date.now()-l.t0)/1000*vtLiveRate(P,st);}
// Множитель разбега: у плеера своя скорость (playbackRate), и допуск расхождения
// считается от неё — на ускоренном превью звук обязан бежать быстрее.
function vtLiveRate(P,st){
  const v=P.vids&&P.vids[0];
  return (v&&+v.playbackRate)||1;}
// Команда живому окну. Тихо: окно могло закрыться между кадром и командой — тогда
// живой звук просто кончился, и об этом скажет опрос панели (`voiceFxHostPoll`).
function vtLiveCmd(P,cmd,at){
  const st=vtOf(P),l=st.live;if(!l)return false;
  const sid=vtLiveSid(P);if(!sid)return false;
  if(cmd==='seek'||cmd==='play'){
    // Перемотка — не чаще порога: ползунок и подводка звука сыплются на каждый кадр, а
    // в трубу окна команда на кадр — это мусор. Стык монтажа и прыжок бегунка
    // приходят не чаще, чем этот порог, и потому не теряются. «Играть» и «пауза» —
    // смена состояния, её терять нельзя: она приходит один раз на нажатие.
    const now=Date.now();
    if(cmd==='play'&&!l.paused&&Math.abs(at-vtLiveExpect(P,st))<vtLiveRate(P,st)*VT_LIVE_DRIFT)
      return false;                       // окно уже играет это место — команда не нужна
    if(cmd==='seek')
      l.sent=now;                         // порог считается только по перемоткам
    l.at=at;l.t0=now;}
  if(cmd==='pause'&&l.paused)return false;   // пауза — одно и то же состояние
  l.paused=(cmd==='pause');
  fetch('/api/voicefx_live',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sid:sid,cmd:cmd,at:(at==null?0:Math.max(0,at))})})
    .catch(e=>uiLog('voicefx_live: '+e));
  return true;}
// Номер сессии живого окна — со страницы (его положила панель, открывшая окно).
function vtLiveSid(P){
  const l=(typeof VOICEFXLIVE!=='undefined')&&VOICEFXLIVE;
  return (l&&l.running&&l.sid)||'';}
// Играть/стоять — командой живому окну: позиция берётся от текущего кадра.
function vtLivePlay(P){if(!vtLiveOn(P))return;vtLiveUpdate(P,vtNow(P));}
function vtLivePause(P){if(!vtLiveOn(P))return;vtLiveCmd(P,'pause');
  const M=vtMuteHost(P);M.liveMute=false;
  const v=M.vids&&M.vids[vtAudioCam(P)];if(v)v.muted=!!M.voiceMute||!!M.silent;}
// Пауза: звук тоже стоит (иначе голос доигрывал бы поверх паузы). Звук камеры на паузе
// возвращаем: при следующем пуске его снова заглушит vtTick.
function vtPause(P){const st=vtOf(P);if(st.el)st.el.pause();
  // Разбег на паузе никому не нужен: он вернётся на ближайшем стыке.
  if(typeof vtSpareStop==='function')vtSpareStop(P);
  vtLivePause(P);vtGate(P,false);}
// Стойка плеера: строка хода голоса рисуется там же, где прогресс сборки прокси —
// блоком поверх кадра. У шага 1 стойка одна: её и занимает редактор, у шага 3 —
// ipvstage. `typeof PV` — не перестраховка: дорожку зовут и стенды, и страница рендера,
// где плеера нарезки нет вовсе.
// Плеер шага 1 — тот, у кого панель «Голос» и живой хост плагинов: теперь это редактор
// (ED), а монтажный PV остался общим объектом кадра, которым редактор и играет. Имя
// vtIsPv оставлено прежним: под ним дорожка голоса живёт в стендах шага 1.
function vtIsPv(P){return (typeof PV!=='undefined'&&P===PV)||(typeof ED!=='undefined'&&P===ED);}
function vtIsEd(P){return typeof ED!=='undefined'&&P===ED;}   // редактор — единственный плеер шага 1
// Кому ставится флаг «звук камеры молчит». Флаг читает camVisual — тот, чей `vids` красит
// кадр, а у шага 1 кадр и ракурс ведёт общий объект PV (редактор ED ведёт только часы и
// дорожку голоса). Без этого camVisual(PV) снова включил бы звук камеры поверх
// обработанного голоса: слышно два голоса разом.
function vtMuteHost(P){return (vtIsEd(P)&&typeof PV!=='undefined'&&PV)?PV:P;}
function vtStage(P){
  if(typeof $!=='function')return null;
  if(vtIsPv(P)||vtIsEd(P))return $('pvstage');
  if(typeof CPV!=='undefined'&&P===CPV)return $('cpvstage');
  return $('ipvstage');}
// Голос играет ВЖИВУЮ (через плагины) — и только пока открыто окно плагина. Окно
// закрыли — хост отдаёт состояние всех плагинов и гаснет, а плеер заказывает
// ИТОГОВЫЙ голос. Дверь одна на весь файл: второй копии правила быть не должно.
//
// Спрашиваем ИМЕННО окно, а не «хост жив»: поднятый хост закрытия окна не замечает,
// и по «хост жив» превью осталось бы на живом звуке навсегда — ровно то, от чего
// уходили. Гашение хоста при этом уже заказано (voiceFxHostPoll).
function vtHostLive(){
  return typeof VOICEFXLIVE!=='undefined'&&!!VOICEFXLIVE&&!!VOICEFXLIVE.window;}
// Живой хост нужен только панели шага 1 (у шага 3 крутить нечего): у неё и ручки,
// и хост плагинов.
function vtLivePrep(P){return vtIsPv(P)||vtIsEd(P);}
// Ждём состояние ВСЕХ плагинов: сервер шлёт хосту `dump_states` и записывает профиль
// сам (`/api/voicefx_live`), а нам нужно дождаться, пока это кончится, — иначе итоговый
// голос закажется под ПРЕЖНИЕ настройки плагинов и вернётся из кеша готовым.
const VT_DUMP_MS=4000;   // потолок ожидания записи накопленного, мс
function vtStatesWait(P){
  const st=vtOf(P);
  // Второй заход поверх первого затирал бы разбудившего: ожидающий один.
  if(st.statesWait)return Promise.resolve(null);
  return new Promise(resolve=>{
    const tmr=setTimeout(()=>{st.statesWait=null;resolve(null);},VT_DUMP_MS);
    st.statesWait=(list)=>{clearTimeout(tmr);st.statesWait=null;resolve(list||[]);};});}
// Пришло состояние всех плагинов — разбудить ожидающего (нет его — некому).
function vtStatesTake(P,list){
  const st=vtOf(P);
  if(st.statesWait)st.statesWait(list);
  return Array.isArray(list)&&list.length>0;}
// Отдать состояние всех плагинов и погасить хост: `voiceFxHostDump` спрашивает у
// сервера состояния (он же пишет профиль), `voiceFxHostStop` снимает процесс по PID.
// Порядок именно такой: снять хост, не забрав накрученное в окнах, — это потерять
// настройки. Возвращает состояние плагинов или null.
async function vtHostDown(P){
  if(typeof voiceFxHostStop!=='function')return null;
  const states=await voiceFxHostDump(P);
  await voiceFxHostStop();
  return states;}
// Голос клипа для превью: готов — играем, нет — просим посчитать и показываем ход.
// Настройки едут телом запроса (vtFx): у монтажа это ручки панели, у шага 3 — профиль.
// Кеш сервера считает трек по СОДЕРЖИМОМУ настроек, поэтому ответ несёт путь с новым
// ключом: смена ручки = новый URL = браузер берёт новый звук сразу, без переоткрытия.
async function vtPrep(P){
  const st=vtOf(P),xml=P.xml,src=vtCam1(P);
  const fx=vtFx(P);
  const seq=++st.seq;
  st.want=JSON.stringify(fx||{});          // под какие настройки просим трек
  // Окно плагина открыто — голос идёт ВЖИВУЮ: играет хост, а плеер подключает
  // ДОРОЖКУ ШУМОДАВА (итоговый трек в это время молчит, иначе цепочка слышна дважды).
  // Окна нет — живой хост не нужен вовсе: все шаги (шаг 1 `ED`, шаг 2, шаг 3 `IPV`)
  // играют ИТОГОВЫЙ голос, запечённый с плагинами. Раньше хост играл всегда, и превью
  // лагало: хост — отдельный процесс, и его не подогнать точнее 0,4 с.
  const live=vtLivePrep(P)&&vtHostLive();
  // Шаг 3 итоговым голосом и был: у него нет ни панели, ни живого хоста.
  const isFinal=!live;
  st.wantFinal=isFinal;                    // какой трек просим — им же помечаем готовый
  // Плагины — вживую: живой хост подгоняется под цепочку панели (поднимается, если
  // нужен, перестраивается на лету, гасится, если плагинов не осталось). Шумодав он
  // НЕ пересчитывает: дорожка шумодава лежит в кеше отдельно от плагинов.
  if(live&&xml&&src&&typeof voiceFxHostSync==='function')voiceFxHostSync(fx,{player:P});
  // Настройки те же, и НУЖНЫЙ трек уже играет: правка касалась только плагинов (или
  // соседней ручки) — просить сервер и переподключать звук незачем. Так «добавил
  // плагин» не мигает «прошу голос клипа…» и не рвёт звук. Тип трека входит в
  // проверку нарочно: после закрытия окна заказан итоговый, и дорожка шумодава,
  // оставшаяся в `path`, за «то же самое» не считается.
  const dn=JSON.stringify((fx&&fx.denoise)||{});
  if(xml&&src&&st.on&&st.path&&st.dn===dn&&!!st.fin===!!isFinal)return;
  // Хост ещё жив, а нужен итоговый голос (окно только что закрыли, ушли со шага):
  // сначала он отдаёт состояние ВСЕХ плагинов в профиль и гаснет, и только потом
  // заказываем голос — иначе он посчитался бы под ПРЕЖНИЕ настройки плагинов и
  // вернулся бы из кеша готовым, а в ушах был бы старый звук.
  if(isFinal&&typeof voiceFxHostOn==='function'&&voiceFxHostOn()){
    // Пока идёт перепекание, играет ПРЕДЫДУЩАЯ запечённая дорожка — своё не отцепляем.
    if(xml&&src&&st.on&&st.path)vtVoiceLine(P,t('голос пересчитывается…'));
    await vtHostDown(P);
    if(seq!==st.seq)return;                // пока гасили, панель перерисовали
    vtPrep(P);return;}
  st.wantDn=dn;
  // Пока печётся новый голос, играет ПРЕДЫДУЩАЯ запечённая дорожка, а не звук камеры:
  // человеку нечего заново слушать сырой звук. Отцепляем дорожку только тогда, когда
  // играть нечего (первый заход, смена клипа) — там играет звук камеры, это честно.
  const keep=!!st.on&&!!st.path;
  if(xml&&src)vtVoiceLine(P,keep?t('голос пересчитывается…'):t('прошу голос клипа…'));
  if(!keep)vtDetach(P);
  vtGate(P,false);                         // пока трек не готов — звук камеры, не тишина
  vtNote(P,'');
  if(!xml||!src){vtVoiceLine(P);return;}
  let d;
  try{
    const reqBody={xml:xml,src:src,fx:fx||{}};
    if(isFinal)reqBody.final=true;
    d=await (await fetch('/api/voicefx_bake',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(reqBody)})).json();}
  catch(e){if(seq===st.seq){vtNote(P,t('голос клипа не посчитан — сервер не ответил'));
      vtVoiceLine(P,t('голос клипа не посчитан — сервер не ответил'));uiLog('voicefx_bake: '+e);}return;}
  if(seq!==st.seq)return;                  // панель перерисовали — ответ не наш
  // Сбой запоминаем: причина (нет окружения RoFormer, сервер занят) могла уйти, а
  // сам vtPrep зовут только правки ручек — «Играть» обязан попробовать заново (edPlay).
  if(d.error){vtVoiceFail(P,errText(d));
    uiLog('voicefx_bake: '+JSON.stringify(d).slice(0,200));return;}
  st.failed=false;
  // Запекание — на каждый клип своё, но СЧИТАЕТСЯ ПО ОДНОМУ: в работе может быть
  // голос другого клипа (открыли соседний, пока считался первый). Ждём свою очередь,
  // а не подхватываем чужой трек: ход ЭТОГО клипа отдаёт /api/voicefx_bake_status.
  vtVoiceShow(P,d,seq);
  if(d.ready||(d.done&&d.path)){vtVoiceTake(P,d.path,seq);return;}
  if(d.running||d.queued){vtVoiceWatch(P,seq);return;}
  // Ни готового, ни счёта: обработка выключена. Строку «прошу голос клипа…» снимаем
  // сами — ответ этой двери хода не несёт, и она осталась бы висеть на кадре.
  vtVoiceLine(P,'');
  vtNote(P,t('обработка выключена — звук камеры как есть'));}
// Ход голоса — ОДНОЙ строкой там, где идут проценты сборки прокси: отдельного окна у
// голоса больше нет (`progOpen` для него не зовётся вовсе). Идут прокси и голос разом —
// две строки: своей формой голос закрывал бы ровно то, что настраивают в превью.
function vtVoiceShow(P,d,seq){
  const st=vtOf(P);
  if(seq!=null&&seq!==st.seq)return;
  const pct=Math.max(0,Math.min(100,Math.round(+d.pct||0)));
  // Проценты есть только тогда, когда их даёт сам шумодав (`N/M` печатает RoFormer).
  // У deep-filter хода работы нет вовсе — показывать «0 %» значило бы врать: полоса
  // идёт «работаю», а в строке написано «голос обрабатывается…» без числа.
  const known=(+d.n>0);
  // «Готово» ход отдаёт ДВУМЯ дверями, и обе надо понимать: заказ (`/api/voicefx_bake`,
  // `ready`) и ход (`/api/voicefx_bake_status`, у него `done` и путь). Признак один —
  // посчитанный трек с путём: по нему превью и играет, и строку снимает.
  if(d.ready||(d.done&&d.path)){st.vtq=false;st.vtend=true;
    vtVoiceLine(P,'');vtNote(P,t('обработанный голос клипа готов'));return;}
  if(d.error){st.vtq=false;st.vtend=true;   // «Играть» попробует снова (edPlay)
    vtVoiceFail(P,d.error);return;}
  if(d.queued&&!d.running){st.vtq=true;st.vtend=false;
    vtVoiceLine(P,t('голос клипа: в очереди'));
    vtNote(P,t('голос клипа: в очереди'));return;}
  if(!d.running&&!d.queued)return;
  st.vtq=false;st.vtend=false;
  let note;
  const isPv=vtIsPv(P)||vtIsEd(P);
  const isRaw = (typeof VOICEFXLIVE!=='undefined' && VOICEFXLIVE && VOICEFXLIVE.track_input==='raw') ||
                (isPv && typeof voiceFxHostOn==='function' && voiceFxHostOn() &&
                 typeof VOICEFXLIVE!=='undefined' && VOICEFXLIVE && VOICEFXLIVE.track_ready);
  if(isPv && isRaw){
    note=known?t('шумодав считается — плагины уже слышно, без очистки, {p} %',{p:pct})
              :t('шумодав считается — плагины уже слышно, без очистки…');
  }else{
    note=known?t('голос обрабатывается, {p} %',{p:pct}):t('голос обрабатывается…');
  }
  vtVoiceLine(P,note,known?pct:null,!!d.running);
  vtNote(P,note);}
// Строка хода голоса на кадре: текст, полоса и «Стоп» (у счёта — свой клип, чужой не
// трогаем). Строку снимает ЗАКОННЫЙ конец (`vtend`), поэтому опрос картинку не затирает.
// Ждём через опрос хода ЭТОГО клипа: у очереди процента нет вовсе — покажем «в очереди».
async function vtVoiceWatch(P,seq){
  const st=vtOf(P);
  if(seq!=null&&seq!==st.seq)return;
  if(st.vtend)return;                        // конец законный (готово / ошибка / «Стоп»)
  clearTimeout(st.poll);
  st.poll=setTimeout(()=>{st.poll=0;vtVoicePoll(P,seq);},VT_POLL);}
async function vtVoicePoll(P,seq){
  const st=vtOf(P);
  if(seq!==st.seq)return;
  let d;
  try{const u='/api/voicefx_bake_status?xml='+encodeURIComponent(P.xml||'');
    d=await (await fetch(u)).json();}
  catch(e){vtVoiceWatch(P,seq);return;}      // сервер не ответил — попробуем ещё
  if(seq!==st.seq)return;
  const was=st.vtend;
  vtVoiceShow(P,d,seq);
  if(d.ready||(d.done&&d.path)){vtVoiceTake(P,d.path,seq);return;}
  if(d.error||was)return;
  if(d.running||d.queued)vtVoiceWatch(P,seq);}
// «Стоп» у строки хода: сервер снимает клип из очереди или гасит текущий счёт по PID
// дочернего процесса. Своё состояние чистим сразу — кнопка не должна ждать ответа.
async function vtVoiceStop(P){
  const st=vtOf(P);st.seq++;st.vtq=false;st.vtend=true;st.want='';
  // Гасим ОБА таймера: и опрос хода, и отложенный пересчёт по правке ручки — иначе
  // «Стоп» тут же заказывал бы счёт заново.
  clearTimeout(st.poll);st.poll=0;
  clearTimeout(st.timer);st.timer=0;
  vtVoiceLine(P,'');vtNote(P,t('обработка остановлена'));
  try{await fetch('/api/voicefx_bake_cancel',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({xml:P.xml||''})});}
  catch(e){uiLog('voicefx_bake_cancel: '+e);}}
// Готовый трек: прежде чем играть — сверить, под ТЕ ЛИ настройки он посчитан. Пока шёл
// счёт, ручки могли поменять (движок, доля, плагины), и играть трек под прежние значит
// врать про то, что слышно — ровно исходная жалоба «сменил настройки, а разницы нет».
// Настройки те же — играем; другие — просим посчитать заново под текущие.
function vtVoiceTake(P,path,seq){
  const st=vtOf(P);
  if(st.want&&st.want!==JSON.stringify(vtFx(P)||{})){vtPrep(P);return;}
  if(path)vtVoiceUse(P,path,seq,st.wantFinal);
  else vtVoiceLine(P,'');}
// Поставить готовый трек в <audio>. Видео не трогаем НИЧЕМ: подмена источника звука и
// подводка к текущей позиции — только у звукового элемента. `seq` сверяется ещё раз
// после загрузки метаданных: за это время мог приехать трек другого клипа.
// `fin` — итоговый это голос (с плагинами) или дорожка шумодава для живого окна: по
// нему vtPrep понимает, тот ли трек уже играет.
function vtVoiceUse(P,path,seq,fin){vtUse(P,path,seq,fin);}
// Строка хода голоса в стойке плеера — отдельной строкой рядом с прогрессом прокси
// (обе — строки общего контейнера `.pvprog` поверх кадра, см. `pvProgRow`). Общей формы
// прогресса у голоса больше нет: окно, которое надо было бы свернуть или закрыть,
// закрывало бы ровно то, что настраивают, — а ход виден и без него.
//
// Идти разом могут оба, и тогда видно две строки друг над другом. Клики контейнер не
// перехватывает (драг вставок и интро под ним), кнопке «Стоп» они включены точечно в CSS.
// Пустой текст снимает строку, а с последней строкой уходит и контейнер; ссылка в
// `st.vtbox` остаётся у снятой строки (пустой, `display:none`) до следующего показа.
function vtVoiceLine(P,text,pct,running){
  const st=vtOf(P),stage=vtStage(P);
  if(!stage)return;
  if(!text){
    if(st.vtbox){st.vtbox.style.display='none';st.vtbox.innerHTML='';}
    pvProgDrop(stage,'voice');return;}
  const box=pvProgRow(stage,'voice','pvpxv');
  box.id=vtIsPv(P)?'pvvoiceline':'ipvvoiceline';st.vtbox=box;
  const known=(pct!=null&&pct>=0);
  const width=known?Math.max(0,Math.min(100,pct)):0;
  box.style.display='';
  box.innerHTML='<div class="pvpx_bar"><span class="pvpx_fill" style="width:'+width+'%"></span></div>'
    +'<div class="pvpx_line"><span class="pvpx_txt">'+esc(text)+'</span>'
    +(running?'<button class="sm pvpx_stop" data-vtstop="1" type="button">'+esc(t('Стоп'))+'</button>':'')
    +'</div>';
  const b=box.querySelector('[data-vtstop]');
  if(b)b.onclick=()=>vtVoiceStop(P);}
// Поставить готовый трек в <audio>. Видео не трогаем НИЧЕМ: подмена источника звука и
// подводка к текущей позиции — только у звукового элемента. `seq` сверяется ещё раз
// после загрузки метаданных: за это время мог приехать трек другого клипа.
function vtUse(P,path,seq,fin){
  const st=vtOf(P);
  if(seq!=null&&seq!==st.seq)return;
  if(!path)return;
  st.on=true;st.dn=st.wantDn;st.fin=!!fin;
  // Надпись панели — под то, что слышно: иначе после счёта (или мгновенно из кеша)
  // висело «голос обрабатывается…», хотя обработанный голос уже играл.
  vtNote(P,t('обработанный голос клипа готов'));
  // Тот же путь — это НЕ повод начинать заново: подмена источника рвёт звук. Но
  // трек мог приехать другим видом (был итоговый, стал дорожка шумодава) — тогда
  // подменяем: `<audio>` держит ровно один источник.
  if(st.path===path&&!!st.fin===!!fin){vtTick(P,vtNow(P));return;}
  st.path=path;
  // Шаг 1: голос играет буфер редактора (eaVoice), своего <audio> у ED нет вовсе.
  if(vtIsEd(P)){if(typeof eaVoice==='function')eaVoice(path);return;}
  const el=vtEl(P);
  el.volume=MEDIA_VOL;
  el.src='/api/media?path='+encodeURIComponent(path);
  const start=()=>{if(seq==null||seq===st.seq)vtTick(P,vtNow(P));};
  if(el.readyState>=1)start();else el.addEventListener('loadedmetadata',start,{once:true});}
// Кадр плеера: подвести звук к позиции ВИДЕО и играть вместе с ним. Позиция — исходное
// время камеры 1 под бегунком (vtSrcAt), оно же время запечённого трека: файл посчитан
// по звуку камеры 1 от её нуля, поэтому и смещения на стыках у них общие.
// Гейт открыт, ПОКА ТРЕК НАШ (`vtGate` смотрит на st.on): на перемотке и догрузке
// <audio> камера молчит — короткая тишина лучше старого голоса.
//
// Синхрон СТРОЖЕ, и это главная правка: расхождение сначала гасится СКОРОСТЬЮ (те же
// ±6 %, что у камер, — на слух незаметно), и только крупное (VT_DRIFT) перемоткой.
// Перемотка — провал в звуке и сброс хвоста у плагинов, и делать её на каждые 0,15 с
// значило ровно то, на что жаловались: «концы не доигрывал, иногда больше играл и не
// там». Точная установка остаётся там, где ждать нельзя: пауза, скраб, вырез (vtSeek).
function vtTick(P,tm){
  const st=vtOf(P);
  // Шаг 1: звук играет буфер редактора (eaSync из edTick), подводить нечего. Остаётся
  // живой хост плагинов (ему — позиция и пуск/пауза) и гейн: окно открыто — свой звук в 0.
  if(vtIsEd(P)){
    if(vtLiveOn(P)||vtMuteHost(P).liveMute!==undefined||st.live)vtLiveUpdate(P,tm);
    if(typeof eaGain==='function')eaGain();
    return;}
  // Окно плагина открыто: голос играет ОН (трек через цепочку в реальном времени),
  // и превью только задаёт ему позицию. Ветка одна на всё: своя дорожка в это время
  // молчит — иначе слышно два голоса разом.
  if(vtLiveOn(P)){vtLiveUpdate(P,tm);return;}
  // Хост ушёл (плагины выключили, превью закрыли): снять его глушение камеры, и звук
  // вернётся дорожке — иначе камера осталась бы немой до следующей правки.
  if(vtMuteHost(P).liveMute!==undefined||st.live)vtLiveUpdate(P,tm);
  if(!st.on||!st.el||!st.path){vtGate(P,false);return;}
  const el=st.el;
  if(vtAudioCam(P)!==0){                 // слушают другую камеру — она звучит сама
    if(!el.paused)el.pause();
    vtGate(P,false);return;}
  vtGate(P,true);
  const at=vtSrcAt(P,tm);
  if(at==null)return;
  // Живому <audio> готовим дублёра ровно так же, как живому <video>: прицел ставит
  // тот, кто знает EDL, — vtSpareSwap (pvStep). Заодно там подмена на стыке и разбег —
  // одной дверью (vtSpareCtl). `typeof` — для стендов: они вырезают из файла по функциям.
  if(typeof vtSpareCtl==='function')vtSpareCtl(P,at,tm);
  if(el.readyState<1)return;             // трек ещё не открылся — молчим, но не камеру
  const live=vtPlaying(P)&&!P.scrubbing;
  if(!live){vtSeek(P,at);if(!el.paused)el.pause();return;}
  const lv=vtOf(P).el||el;   // дублёр мог выйти в эфир прямо сейчас
  vtRate(P,at);
  if(lv.paused)lv.play().catch(()=>{});}
// Точная установка дорожки: своё время, скорость в норму. Так ставят пауза, скраб и
// прыжок через вырез с перемоткой — там ждать порога нечего, звук обязан оказаться
// ровно на месте, а не подъезжать.
function vtSeek(P,at){
  const el=vtOf(P).el;if(!el)return;
  el.playbackRate=1;
  const want=Math.max(0,at);
  // Пока идёт своя перемотка, вторую не шлём: запрос на каждый кадр сбрасывает
  // уже готовый кадр звука (у камер это же правило, camTrack).
  if(!el.seeking&&Math.abs(el.currentTime-want)>0.005){try{el.currentTime=want;}catch(e){}}}
// Подводка СКОРОСТЬЮ: маленькое расхождение гасится ±6 % (как у камер), крупное —
// перемоткой. Скорость трогаем только у играющего звука: на паузе она бессмысленна.
function vtRate(P,at){
  const el=vtOf(P).el;if(!el)return;
  const d=el.currentTime-Math.max(0,at),ad=Math.abs(d);
  if(el.seeking||ad>VT_DRIFT){el.playbackRate=1;
    if(el.seeking)return;
    // Крупный разрыв скоростью не догнать: перемотка, и ОДНА (пока идёт — не повторяем).
    if(ad>VT_SOFT)vtSeek(P,at);
    return;}
  el.playbackRate=(ad<=VT_SOFT)?1:(d<0?1+VT_RATE:1-VT_RATE);}
// Пересчёт после правки ручки панели: ~400 мс затишья. Ползунок сыплется на каждый
// пиксель, а голос клипа — это счёт шумодава на весь клип: запрос на каждое движение
// ставил бы их в очередь. Кеш по настройкам делает повторный вызов дешёвым: те же
// ручки — тот же файл.
// Тут же и АВТОСОХРАНЕНИЕ профиля спикера (`pvVoiceAuto`): ручку отпустили — она уже
// у спикера, а кнопки «Сохранить у спикера» больше нет. Две задержки у одного
// события, и обе нужны: сохранение — это файл профиля (быстро), пересчёт голоса —
// минуты счёта (дорого), поэтому оно и уезжает отдельным вызовом.
function pvVoiceTune(){
  if(typeof voiceFxStatus==='function')voiceFxStatus($('pvvoice'),'');
  if(typeof vtNote==='function'&&typeof ED!=='undefined')vtNote(ED,'');
  const st=vtOf(ED);
  clearTimeout(st.timer);
  st.timer=setTimeout(()=>{st.timer=0;vtPrep(ED);},VT_QUIET);
  if(typeof pvVoiceAuto==='function')pvVoiceAuto();}
// «Сохранить у спикера» позвало: профиль записан, и по нему печётся `<стем>.voice.wav`.
// Сборку превью-прокси при этом НЕ заказываем: прокси всегда со звуком камеры и от
// настроек голоса не зависит. Раньше заказывали — и каждая правка ручки тянула за собой
// пересборку файла камеры, а на экране висело «сборка прокси 1/1 · 0 %».
async function pvVoiceBake(){
  await vtPrep(ED);
  if(!vtOf(ED).on)vtNote(ED,t('обработка выключена — голос клипа не печётся'));}
// Число ли слово — для кнопки счётчика «123» на чипе. Общая на панель слов шага AE
// и на разметку строк интро; второй копии правила в JS нет.
function isNumberWord(text){
  if(!text)return false;
  const s=(''+text).replace(/\s+/g,' ').trim();
  if(!s)return false;
  let dots=0,commas=0;
  for(let i=0;i<s.length;i++){if(s[i]==='.')dots++;else if(s[i]===',')commas++;}
  if(dots+commas>1)return false;
  const sep=(dots===1?'.':(commas===1?',':null));
  if(sep){
    const parts=s.split(sep);
    if(parts.length!==2)return false;
    const intClean=parts[0].replace(/ /g,'');
    const frac=parts[1];
    if(!intClean||!/^[0-9]+$/.test(intClean)||!frac||!/^[0-9]+$/.test(frac))return false;
    return true;
  }else{
    const intClean=s.replace(/ /g,'');
    if(!intClean||!/^[0-9]+$/.test(intClean))return false;
    return true;
  }
}
function wordBreakEl(cfg,prev,o){
  const b=document.createElement('span');
  b.className='brk';b.textContent='|';b.dataset.t=t('Стопка (клик — разрыв · Ctrl+клик — склейка)');
  b.dataset.a=cfg.words.indexOf(prev);b.dataset.b=cfg.words.indexOf(o);
  b.tabIndex=0;b.setAttribute('role','button');
  const toggleBreak=()=>{
    if(cfg.brk.has(prev.i)){
      cfg.brk.delete(prev.i);
    } else {
      cfg.brk.add(prev.i);
      cfg.jns.delete(prev.i);
    }
    if(cfg.syncBreak)cfg.syncBreak();
  };
  const toggleJoin=()=>{
    if(cfg.jns.has(prev.i)){
      cfg.jns.delete(prev.i);
    } else {
      cfg.jns.add(prev.i);
      cfg.brk.delete(prev.i);
    }
    if(cfg.syncBreak)cfg.syncBreak();
  };
  b.onclick=(e)=>{if(e&&(e.ctrlKey||e.metaKey)){e.preventDefault();toggleJoin();}else{toggleBreak();}};
  b.onkeydown=(e)=>{if(e.key!=='Enter')return;e.preventDefault();if(e.ctrlKey||e.metaKey){toggleJoin();}else{toggleBreak();}};
  return b;
}
function wordChipEl(cfg,o,k){
  const wi=cfg.words.indexOf(o);
  const c=document.createElement('span');c.className='chip';c.dataset.k=k;
  c.dataset.wi=wi;
  c.dataset.t=cfg.tip?cfg.tip(o):t('{s}с · двойной клик — в интро · Ctrl+клик — правка текста',{s:o.start});
  c.tabIndex=0;c.setAttribute('role','button');
  const txtSpan=document.createElement('span');txtSpan.textContent=o.w;c.appendChild(txtSpan);
  if(isNumberWord(o.w)){
    const cntBtn=document.createElement('span');
    cntBtn.className='chip-cnt';
    cntBtn.textContent='123';
    cntBtn.onclick=(e)=>{if(cfg.syncCount)cfg.syncCount(wi,e);};
    cntBtn.ondblclick=(e)=>{e.stopPropagation();};
    c.appendChild(cntBtn);
  }
  c.onclick=(e)=>{if(cfg.chipClick)cfg.chipClick(o,wi,c,e);};
  c.onkeydown=(e)=>{if(e.key!=='Enter')return;e.preventDefault();
    if(cfg.chipKeydown)cfg.chipKeydown(o,wi,c,e);};
  return c;
}
function wordsPaint(host,cfg){if(!host)return;
  const words=cfg.words,hl=cfg.hl,brk=cfg.brk,cnt=cfg.cnt,jns=cfg.jns;
  [...host.children].forEach(el=>{
    if(el.classList.contains('chip')){
      const o=words[+el.dataset.wi];
      if(o){
        el.classList.toggle('on',hl.has(o.i));
        const cntEl=el.querySelector('.chip-cnt');
        if(cntEl){
          const on=cnt&&cnt.has(o.i);
          cntEl.classList.toggle('on',on);
          cntEl.dataset.t=on?t('Счётчик включён (клик — выключить)'):t('Счётчик (клик — включить)');
        }
      }
      return;
    }
    if(!el.classList.contains('brk'))return;
    const a=words[+el.dataset.a],b=words[+el.dataset.b];
    const pair=!!(a&&b&&hl.has(a.i)&&hl.has(b.i));
    el.classList.toggle('hid',!pair);
    if(pair){
      const isBrk=brk&&brk.has(a.i);
      const isJns=jns&&jns.has(a.i);
      el.classList.toggle('on',isBrk);
      el.classList.toggle('jns',isJns);
      if(isJns){
        el.textContent='';
        el.style.color='';
        el.dataset.t=t('Склейка в строку (Ctrl+клик — снять склейку · клик — разрыв)');
      } else if(isBrk){
        el.textContent='|';
        el.style.color='';
        el.dataset.t=t('Разрыв стопки (клик — снять разрыв · Ctrl+клик — склейка)');
      } else {
        el.textContent='|';
        el.style.color='';
        el.dataset.t=t('Стопка (клик — разрыв · Ctrl+клик — склейка)');
      }
    } else {
      el.classList.remove('on','jns');
    }
  });}
function introWordEdit(el,word,save,cancel){
  if(!el||el.tagName==='INPUT')return;
  const inp=document.createElement('input');inp.type='text';inp.value=word;
  inp.className='iwordedit';inp.style.width=Math.max(60,word.length*10+22)+'px';
  let done=false;                                  // Esc/сохранение уже закрыли — blur не должен сохранять второй раз
  const commit=()=>{if(done)return;done=true;save(inp.value);};
  inp.onkeydown=(e)=>{e.stopPropagation();
    if(e.key==='Enter'){commit();}
    else if(e.key==='Escape'){done=true;cancel();}};
  inp.onblur=commit;                               // клик мимо = закрыть и сохранить
  inp.onclick=(e)=>e.stopPropagation();
  el.replaceWith(inp);inp.focus();inp.select();}
function aewEditIntroWord(el,wi){const o=WORDS[wi];if(!o)return;
  introWordEdit(el,o.w,s=>aewSaveWord(o,s),()=>aewRender());}
// Счётчик — на КАЖДОЕ слово-число строки: cnt_words хранит ПОЗИЦИИ слов внутри строки
// (0..count-1). Раньше это был один флаг строки на всё интро, и клик снимал счётчик со
// ВСЕХ остальных строк (правило «один счётчик на интро»); оно отменено — строки и кнопки
// независимы. p0 — позиция первого числа строки: без неё первую (легаси) разметку
// is_count нельзя превратить в список, и клик по второму числу потерял бы первое.
function introToggleCount(rows, i, p, p0){
  if(!rows||!rows[i])return;
  const r=rows[i];
  let list=Array.isArray(r.cnt_words)?r.cnt_words.slice():[];
  if(!Array.isArray(r.cnt_words)&&r.is_count&&p0!=null)list=[p0];
  const at=list.indexOf(p);
  if(at>=0)list.splice(at,1);else list.push(p);
  list.sort((a,b)=>a-b);
  r.cnt_words=list;                 // is_count всегда = «в списке есть позиции»
  r.is_count=list.length>0;
}
function introRowSetAnim(rows, i, val){
  if(!rows||!rows[i])return;
  rows[i].anim=val;
  if(val==='glitch'){
    let head=i;
    while(head>0&&!introIsHead(rows,head))head--;
    const end=introGroupEnd(rows,head);
    for(let k=head;k<end;k++){
      if(k!==i&&rows[k])rows[k].back=true;
    }
  }
}
function introRowHtml(cfg,r,i,idxs,gi){
  const A=cfg.arr,S=cfg.sync,W=cfg.words;
  const ws=idxs.map(x=>W[x].w),mid=(r.from!=null);
  const p0=idxs.findIndex(x=>isNumberWord(W[x].w));   // позиция первого числа строки: она же легаси-счётчик
  // «Добавить слово слева»: у акцента строка начинается со своего `from`, у обычной — с первого
  // слова диапазона (idxs). Слово 0 — слева брать нечего, кнопка гаснет (та же проверка, что
  // в introGrowLeft: кнопка и функция обязаны гаснуть на одном и том же слове).
  const firstW=(r.from!=null&&r.from>=0)?r.from:(idxs.length?idxs[0]:null);
  const canLeft=(firstW!=null&&firstW>0);
  const sep=(i===0)?''
    :(mid?'<div class="xfade"><span class="bar"></span>'+ico('target')+t(' в середине')+'<span class="bar"></span></div>'
    :(r.break
      ?'<div class="xfade"><span class="bar"></span>'+ico('xfade')+t(' кросс-фейд ')+'<span class="rm" tabindex="0" role="button" aria-label="'+t('Убрать разрыв')+'" data-t="'+t('Убрать разрыв')+'" onclick="'+A+'['+i+'].break=false;'+S+'">'+ico('x')+'</span><span class="bar"></span></div>'
      :'<div class="xfade sep" tabindex="0" role="button" data-t="'+t('Разделить на два прекомпа')+'" onclick="'+A+'['+i+'].break=true;'+S+'"><span class="bar"></span>'+t('разделить')+'<span class="bar"></span></div>'));
  const fromBadge=mid
    ?'<span class="tag on" tabindex="0" role="button" style="cursor:pointer" aria-label="'+t('Убрать привязку')+'" data-t="'+t('Убрать привязку к слову')+'" onclick="'+A+'['+i+'].from=null;'+cfg.clearPick+';'+S+'">'+t('с «{w}»',{w:esc((W[r.from]||{}).w||('#'+r.from))})+ico('x')+'</span>'
    :'<button class="icon" data-t="'+t('Начать группу со слова из середины')+'" aria-label="'+t('Начать группу со слова из середины')+'" style="'+(cfg.pick===i?'color:var(--introhl,var(--subhl,var(--yel)))':'')+'" onclick="'+cfg.arm(i)+'">'+ico('target')+'</button>';
  const colVal = r.color || 'white';
  const animVal = r.anim || '';
  const colorSelect = '<select style="min-width:0;width:76px;min-height:var(--h-sm);height:var(--h-sm);padding:2px 4px;font-size:12px;flex-shrink:0" aria-label="'+t('Цвет')+'" data-t="'+t('Цвет строки')+'" onchange="'+A+'['+i+'].color=this.value;if(this.value===\'custom\'&&!'+A+'['+i+'].fill)'+A+'['+i+'].fill=[1,1,1];this.blur();'+S+'">'
    +'<option value="white" '+(colVal==='white'?'selected':'')+'>'+t('белый')+'</option>'
    +'<option value="yellow" '+(colVal==='yellow'?'selected':'')+'>'+t('хайлайт')+'</option>'
    +'<option value="accent" '+(colVal==='accent'?'selected':'')+'>'+t('акцент')+'</option>'
    +'<option value="custom" '+(colVal==='custom'?'selected':'')+'>'+t('свой')+'</option>'
    +'</select>';
  const customColorInput = (colVal==='custom')
    ?'<input type="color" style="width:24px;height:24px;min-height:0;padding:0;border:1px solid var(--bd2);border-radius:var(--r-sm);cursor:pointer;flex-shrink:0" aria-label="'+t('Свой цвет')+'" data-t="'+t('Свой цвет строки')+'" value="'+(typeof rgb2hex==='function'?rgb2hex(r.fill||[1,1,1]):'#ffffff')+'" oninput="'+A+'['+i+'].fill=(typeof hex2rgb===\'function\'?hex2rgb(this.value):[1,1,1]);'+S+'">'
    :'';
  const animSelect = '<select style="min-width:0;width:84px;min-height:var(--h-sm);height:var(--h-sm);padding:2px 4px;font-size:12px;flex-shrink:0" aria-label="'+t('Появление')+'" data-t="'+t('Появление строки')+'" onchange="introRowSetAnim('+A+','+i+',this.value);this.blur();'+S+'">'
    +'<option value="" '+(animVal===''?'selected':'')+'>'+t('фейд')+'</option>'
    +'<option value="up" '+(animVal==='up'?'selected':'')+'>'+t('вверх')+'</option>'
    +'<option value="left" '+(animVal==='left'?'selected':'')+'>'+t('слева')+'</option>'
    +'<option value="right" '+(animVal==='right'?'selected':'')+'>'+t('справа')+'</option>'
    +'<option value="glitch" '+(animVal==='glitch'?'selected':'')+'>'+t('глитч')+'</option>'
    +'<option value="reveal" '+(animVal==='reveal'?'selected':'')+'>'+t('раскрытие')+'</option>'
    +'</select>';
  const textColor = (colVal==='yellow'?'var(--introhl,var(--subhl,var(--yel)))':(colVal==='accent'?'var(--subhl3,#af1f1f)':(colVal==='custom'&&r.fill&&typeof rgb2hex==='function'?rgb2hex(r.fill):'var(--tx)')));
  return sep+'<div class="row introrow" data-ig="'+(gi-1)+'" style="align-items:center;margin-top:6px;flex-wrap:nowrap;gap:6px">'
    +introPlus(cfg.rows,i,cfg.add)
    +'<input type="number" min="0" step="1" style="width:48px;min-height:var(--h-sm);height:var(--h-sm);padding:2px 4px;text-align:center;flex-shrink:0" aria-label="'+t('Слов в строке')+'" value="'+r.count+'" oninput="'+A+'['+i+'].count=parseInt(this.value)||0;'+S+'">'
    +colorSelect
    +customColorInput
    +animSelect
    +'<label class="chk" style="margin:0;padding:0 4px;flex-shrink:0" data-t="'+t('Акцентный шрифт: другой шрифт и регистр этой строки (accent_font стиля; пусто = выключено)')+'"><input type="checkbox" aria-label="'+t('Акцентный шрифт')+'" '+(r.accent?'checked':'')+' onchange="'+A+'['+i+'].accent=this.checked;this.blur();'+S+'"></label>'
    +'<label class="chk" style="margin:0;padding:0 4px;flex-shrink:0" data-t="'+t('Задний план: строка уходит на задний план (шрифт back_font и регистр back_case из стиля)')+'"><input type="checkbox" aria-label="'+t('Задний план')+'" '+(r.back?'checked':'')+' onchange="'+A+'['+i+'].back=this.checked;this.blur();'+S+'"></label>'
    +'<label class="chk" style="margin:0;padding:0 4px;flex-shrink:0" data-t="'+t('Большое слева: строка встаёт слева крупно, остальные строки группы — стопкой справа (высота — по стопке)')+'"><input type="checkbox" aria-label="'+t('Большое слева')+'" '+(r.big?'checked':'')+' onchange="'+A+'['+i+'].big=this.checked;this.blur();'+S+'"></label>'
    +fromBadge
    // «Стрелка влево +» слева от слов: забрать предыдущее слово в эту строку. У первой строки
    // интро кнопка не исчезает, а гаснет (disabled) — вёрстка строк не скачет.
    +'<button class="icon" style="flex-shrink:0" aria-label="'+t('Добавить слово слева')+'" data-t="'+t('Добавить слово слева: забрать предыдущее слово в эту строку')+'"'+(canLeft?'':' disabled')+' onclick="'+cfg.grow+'('+i+')">'+ico('arrow_left_plus')+'</button>'
    // Слова строки — кликабельные: в общем списке их уже нет (introConsumed), и править
    // текст слова, ушедшего в интро, было негде вовсе. Клик = тот же /api/edit_word.
    +'<span class="grow" style="min-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:'+textColor+'">'
    +(ws.length?idxs.map((x,p)=>{
        const isNum=isNumberWord(W[x].w);
        const wHtml='<span class="iword" tabindex="0" role="button" data-t="'+t('Клик — изменить текст слова (Enter — сохранить, Esc — отмена)')+'"'
          +' onclick="'+cfg.edit+'(this,'+x+')"'
          +' onkeydown="if(event.key===\'Enter\'){event.preventDefault();'+cfg.edit+'(this,'+x+')}">'+esc(W[x].w)+'</span>';
        if(isNum){
          // Чип горит по СВОЕЙ позиции в cnt_words; без списка (старые задания) — по
          // легаси-флагу строки и попаданию в первое число.
          const on=Array.isArray(r.cnt_words)?r.cnt_words.includes(p):(!!r.is_count&&p===p0);
          const tip=on?t('Счётчик включён (клик — выключить)'):t('Счётчик (клик — включить)');
          return wHtml+'<span class="icnt '+(on?'on':'')+'" tabindex="0" role="button" data-t="'+tip+'"'
            +' onclick="event.stopPropagation();introToggleCount('+A+','+i+','+p+','+p0+');'+S+'">123</span>';
        }
        return wHtml;
      }).join(' ')
      :'—')
    +'</span>'
    +'<span class="x" tabindex="0" role="button" aria-label="'+t('Удалить строку')+'" data-t="'+t('Удалить строку интро')+'" style="flex-shrink:0" onclick="'+A+'.splice('+i+',1);'+S+'">'+ico('x')+'</span></div>';}

