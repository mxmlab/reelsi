// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// перенос localStorage на ключи Reelsi, i18n, общие хелперы
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.
// Порядок важен — объявления функций поднимаются в пределах своего файла.

// ================= localStorage: переезд с ключей AutoCut на Reelsi =================
// Состояние страницы (шаг мастера, набор, громкость, настройки базы вставок) живёт
// ТОЛЬКО в localStorage — с диска до него не дотянуться. Переименовать ключи без
// переноса значит стереть юзеру всю его работу молча: открыл после обновления —
// пустой мастер. Переносим один раз, старые ключи не удаляем (откат на прошлую
// версию не должен обнулять состояние).
(function migrateLS(){
  try{
    ['step','vol','inslib','state'].forEach(function(k){
      const oldK='autocut2_'+k, newK='reelsi_'+k;
      if(localStorage.getItem(newK)===null){
        const v=localStorage.getItem(oldK);
        if(v!==null) localStorage.setItem(newK,v);
      }
    });
  }catch(e){}
})();

// ================= i18n =================
// Ключ словаря — САМ РУССКИЙ ТЕКСТ, как в gettext. Поэтому разметка и код остаются
// на русском, а английский накладывается поверх по словарю static/i18n/en.json.
// Следствия, ради которых так и сделано:
//   - русский интерфейс работает вообще без словаря (fallback = ключ), и правка
//     перевода физически не может его сломать;
//   - при lang='ru' ниже НИЧЕГО не выполняется: ни загрузки, ни обхода DOM,
//     ни наблюдателя — нулевая цена для того, кто работает по-русски;
//   - нет состояния «ключ есть, перевода нет» с пустой дыркой на экране.
// Плата: правка русского текста рвёт связь с переводом. Видно сразу (строка
// вернулась на русский), чинится дописыванием ключа: tools/i18n_extract.py.
let LANG = 'ru', I18N = {}, I18N_OBS = null;
const I18N_ATTRS = ['data-t', 'placeholder', 'title', 'aria-label'];

// t('Камера {n}', {n: 2}) — подстановка нужна там, где строка собиралась
// конкатенацией: наблюдатель такие не ловит (готовый текст «Камера 2» не совпадает
// ни с одним ключом), да и порядок слов в переводе может быть другим.
function t(s, vars){
  let out = (s && LANG === 'en' && I18N[s]) || s;
  if(vars){
    for(const k in vars){
      const v = vars[k];
      out = out.replace(new RegExp('\\{' + k + '(?::(?:\\.(\\d+))?[fds])?\\}', 'g'), function(m, prec){
        if(prec != null && typeof v === 'number') return v.toFixed(parseInt(prec, 10));
        return v != null ? String(v) : '';
      });
    }
  }
  return out;
}

// Ошибка с бэкенда: {error:'русский текст', err:'код', err_vars:{…}}. Динамические
// сообщения (имя файла и т.п.) ключом «текст» не ловятся — бэкенд кладёт рядом
// машинный код (umsg.py), и перевод берётся по нему: ERR_<код> в словаре. Нет кода
// или нет перевода — берём русский текст как ключ, нет и его — как есть.
function errText(d){
  if(!d) return '';
  if(d.err && LANG === 'en' && I18N['ERR_' + d.err]){
    return t('ERR_' + d.err, d.err_vars || {});
  }
  return t(d.error);
}

// Строка лога: сервер может слать объект {t:'шаблон', v:{…}} или готовую строку.
function fmtLog(l){
  if(l == null) return '';
  if(typeof l === 'object' && l.t) return t(l.t, l.v || {});
  return t(String(l));
}

// Задание, оборванное перезапуском сервера: сервер помнит его в журнале заданий
// (job_state.json) и отдаёт в статусе полем interrupted — с именем элемента и
// прогрессом. Без этой строки оборванная нарезка выглядела бы как «ничего не
// запускалось», и работу начинали бы заново поверх незакрытой.
function jobInterrupted(x){
  if(!x) return;
  const p = x.progress || {};
  const where = [x.label || '', x.item || '', (p.n ? t('клип {i} из {n}', {i:p.i, n:p.n}) : '')]
    .filter(Boolean).join(' · ');
  const msg = t('прервано перезапуском сервера');
  uiLog('⚠ ' + msg + (where ? ': ' + where : ''));
  toast(msg + (where ? ' — ' + where : ''));
}

// Переводим ТОЛЬКО текстовые узлы и известные атрибуты. Разметку внутри элементов
// не трогаем — иначе любой innerHTML с обработчиками превратился бы в текст.
function applyI18n(root){
  if(LANG === 'ru' || !root) return;
  if(root.nodeType === 1 && root.closest && root.closest('[data-noi18n]')) return;
  if(root.nodeType === 3 && ((root.parentElement && root.parentElement.closest('[data-noi18n]')) || (root.parentNode && root.parentNode.closest && root.parentNode.closest('[data-noi18n]')))) return;
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const hits = [];
  for(let n = walk.nextNode(); n; n = walk.nextNode()){
    if((n.parentElement && n.parentElement.closest('[data-noi18n]')) || (n.parentNode && n.parentNode.closest && n.parentNode.closest('[data-noi18n]'))) continue;
    const raw = n.nodeValue, s = raw.trim();
    if(!s || !I18N[s]) continue;
    // пробелы по краям сохраняем: на них держится вёрстка строк вида «<иконка> Видео»
    hits.push([n, raw.replace(s, I18N[s])]);
  }
  hits.forEach(([n, v]) => { n.nodeValue = v; });
  const els = root.querySelectorAll ? root.querySelectorAll('*') : [];
  const all = root.nodeType === 1 ? [root, ...els] : els;
  all.forEach(el => {
    if(!el.getAttribute) return;
    if(el.closest && el.closest('[data-noi18n]')) return;
    I18N_ATTRS.forEach(a => {
      const v = el.getAttribute(a);
      if(v && I18N[v]) el.setAttribute(a, I18N[v]);
    });
  });
}

// Интерфейс дорисовывается на лету (клипы, вставки, история задач), и переводить
// каждую точку вставки руками — тысяча правок и гарантированно забытые места.
// Наблюдатель ловит всё добавленное разом. Включается только для не-русского.
function startI18nObserver(){
  if(LANG === 'ru' || I18N_OBS) return;
  I18N_OBS = new MutationObserver(muts => {
    for(const m of muts) m.addedNodes.forEach(n => {
      if(n.nodeType === 1){
        if(n.closest && n.closest('[data-noi18n]')) return;
        applyI18n(n);
      }
      else if(n.nodeType === 3){
        if((n.parentElement && n.parentElement.closest('[data-noi18n]')) || (n.parentNode && n.parentNode.closest && n.parentNode.closest('[data-noi18n]'))) return;
        if(I18N[(n.nodeValue||'').trim()]) applyI18n(n.parentNode);
      }
    });
  });
  I18N_OBS.observe(document.body, {childList: true, subtree: true});
}

async function setLang(lang, reapply){
  LANG = (lang === 'en') ? 'en' : 'ru';
  try{ localStorage.setItem('reelsi_lang', LANG); }catch(e){}
  document.documentElement.setAttribute('lang', LANG);
  if(LANG === 'en' && !Object.keys(I18N).length){
    // словарь встроен в страницу (см. _dict_en в webui.py) — берём синхронно,
    // fetch тут создавал бы гонку с первой отрисовкой
    try{ I18N = (typeof REELSI_I18N !== 'undefined' && REELSI_I18N) || {}; }
    catch(e){ I18N = {}; }    // нет словаря — остаёмся на русском, а не падаем
  }
  // Переключение вручную — ТОЛЬКО перезагрузкой, в обе стороны. Обход готового DOM
  // догоняет лишь целые текстовые узлы, а строки, собранные конкатенацией
  // (`t('Камера ')+(k+1)`, `t('Папки: ')+…`, сводки ⚙), ни одному ключу не равны и
  // после переключения оставались русскими до F5 (поймано 2026-08-11). После
  // перезагрузки их считает уже сам t() при верном LANG — см. initLang ниже.
  const btn = document.getElementById('langbtn');
  // Кнопка показывает текущий язык, а не действие, которое будет выполнено.
  if(btn) btn.textContent = LANG === 'en' ? 'EN' : 'RU';
  if(reapply){ location.reload(); return; }
  if(LANG === 'en'){ applyI18n(document.body); startI18nObserver(); }
}

function toggleLang(){ setLang(LANG === 'en' ? 'ru' : 'en', true); }

// LANG и словарь ставим НЕМЕДЛЕННО, а не по DOMContentLoaded: иначе первые t() —
// в отрисовке строк камер и статуса — успевают отработать при LANG='ru' и вернуть
// ключ. Обход готового DOM их уже не догонит: «Камера 1» собрана из «Камера {n}».
(function initLang(){
  let saved = null;
  try{ saved = localStorage.getItem('reelsi_lang'); }catch(e){}
  const defLang = (typeof REELSI_DEFAULT_LANG !== 'undefined' && REELSI_DEFAULT_LANG) || 'ru';
  const target = (saved === 'ru' || saved === 'en') ? saved : defLang;
  if(target !== 'en'){
    LANG = 'ru';
    document.addEventListener('DOMContentLoaded', () => {
      document.documentElement.setAttribute('lang', 'ru');
      const btn = document.getElementById('langbtn');
      if(btn) btn.textContent = 'RU';
    });
    return;
  }
  LANG = 'en';
  try{ I18N = (typeof REELSI_I18N !== 'undefined' && REELSI_I18N) || {}; }catch(e){}
  document.addEventListener('DOMContentLoaded', () => {
    document.documentElement.setAttribute('lang', 'en');
    const btn = document.getElementById('langbtn');
    if(btn) btn.textContent = 'EN';
    applyI18n(document.body);
    startI18nObserver();
  });
})();

// ================= helpers =================
function $(id){return document.getElementById(id);}
function val(id){const el=$(id);return el?el.value:'';}
// ' тоже экранируем: разметка собирается конкатенацией с onclick-строками, и первый же
// путь с апострофом, попавший в обработчик, ломал бы кавычки
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;');}
function nCams(){return parseInt(document.querySelector('input[name=cams]:checked').value);}
function fmtT(s){s=Math.max(0,Math.floor(s));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');}
function fmtIns(s){s=Math.round(Math.max(0,s)*10)/10;const m=Math.floor(s/60);const r=s-m*60;
  let sec=r.toFixed(1).replace(/\.0$/,'');if(r<10)sec='0'+sec;return m+':'+sec;}

// ================= таймлайны: один вид на три полосы времени =================
// Окно подкаста («Вся запись»), превью вставок и редактор нарезки рисуют одно и то же:
// линейку, плейхед с флажком, значки зума и одну раскладку управления мышью. Общее — здесь.
// Цвета: CSS-переменные --tl-* в :root (app.css) и TL_COL ниже читают их через
// getComputedStyle; запасные значения — на случай, когда стилей нет (стенды в node).
const TL_COL = (function(){
  let cs = null;
  try{ cs = getComputedStyle(document.documentElement); }catch(e){ cs = null; }
  const g = (name, fb) => {
    let v = '';
    try{ v = cs ? cs.getPropertyValue(name) : ''; }catch(e){ v = ''; }
    v = String(v || '').trim();
    return v || fb;
  };
  return {
    // общее для всех трёх полос
    ph: g('--tl-ph', '#6ea8ff'),          // плейхед: синий, с флажком сверху
    rule: g('--tl-rule-bg', '#0a0a0a'),   // фон линейки
    ruleLine: g('--tl-rule-line', '#212121'),
    tick: g('--tl-tick', '#2b2b2b'),      // риски линейки
    tickTx: g('--tl-tick-tx', '#6f6f6f'), // подписи линейки
    // фон полосы и волна — прежние цвета редактора шага 1 (70-editor.js), не данные типов
    bg: g('--tl-bg', '#0d0d0d'),
    ribbon: g('--tl-ribbon', '#101010'), bubble: g('--tl-bubble', '#141414'),
    wave: '#6fce9e', waveSel: '#9fc0ff', waveOff: '#3f3f3f',
    blkFill: 'rgba(60,160,110,.08)', blkEdge: '#3ca06e',
    selFill: 'rgba(110,168,255,.14)', selEdge: '#6ea8ff',
    active: '#e8e8e8'
  };
})();
const TL_RULER_PX = 11;    // кегль подписей линейки, css px (ниже 11 не опускаем — DESIGN)
const TL_TICK_MIN = 64;    // подписи линейки не теснее этого, css px
const TL_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

// Шаг подписей линейки, с: наименьший из набора, при котором подписи стоят не теснее
// TL_TICK_MIN css px. spanSec — длина видимого окна, widthPx — его ширина в css px.
// Пример: 2 ч на 1300 px → 600 с; 79 с на 1300 px → 5 с (тесты — test_podcast_editor_js.py).
function tlTickStep(spanSec, widthPx){
  const span = Number(spanSec), w = Number(widthPx);
  const pps = (span > 0 && w > 0) ? w / span : 0;
  return TL_STEPS.find(s => s * pps >= TL_TICK_MIN) || TL_STEPS[TL_STEPS.length - 1];
}

// Подпись линейки. totalSec — длина ВСЕЙ записи (не окна): формат не должен меняться
// при зуме. От часа — h:mm:ss, короче — m:ss. stepSec — шаг рисок окна: меньше 1 с — с
// десятыми (1:15.5). Раньше подпись округляла до целой секунды, и при шаге 0,5 с две риски
// подряд получали одно и то же «1:15», «1:15», «1:16», «1:16». Без stepSec — как было.
function tlRulerLabel(sec, totalSec, stepSec){
  const fine = Number(stepSec) > 0 && Number(stepSec) < 1;
  const tenths = fine ? Math.max(0, Math.round((Number(sec) || 0) * 10)) : Math.max(0, Math.floor(Number(sec) || 0)) * 10;
  const x = Math.floor(tenths / 10);
  const frac = fine ? '.' + (tenths % 10) : '';
  if(Number(totalSec) >= 3600){
    return Math.floor(x / 3600) + ':' + String(Math.floor(x / 60) % 60).padStart(2, '0') + ':' + String(x % 60).padStart(2, '0') + frac;
  }
  return fmtT(x) + frac;
}

// Окно [v0,v1] в пределах записи [0,dur]: длина не меньше minSpan (и не больше записи), сдвиг —
// чтобы не выйти за края. Запись без длины (dur ≤ 0) — окно как есть. Минимум — у каждой полосы
// свой: подкаст — POD_VIEW_MIN, редактор — 1.5 с. Возвращает {v0,v1}.
function tlClampView(v0,v1,dur,minSpan){
  const mn=Number(minSpan)||0;
  let span=Number(v1)-Number(v0);
  if(!isFinite(span)||span<=0)span=mn;
  const D=dur>0?dur:span;
  span=Math.min(D,Math.max(Math.min(mn,D),span));
  let a=Number(v0);if(!isFinite(a))a=0;
  a=Math.max(0,Math.min(a,D-span));
  return {v0:a,v1:a+span};
}
// Масштаб окна: f > 1 — ближе, f < 1 — дальше. atF — доля ширины окна, где курсор (null — центр).
// ПОЧЕМУ так: точка под курсором остаётся на месте, иначе колесо уводит запись из-под пальца.
// minSpan — минимум окна полосы (подкаст — POD_VIEW_MIN, редактор — 1.5 с). Возвращает {v0,v1}.
function tlZoomSpan(v0,v1,f,atF,dur,minSpan){
  const mn=Number(minSpan)||0;
  const span0=Number(v1)-Number(v0);
  const D=dur>0?dur:span0;
  const a=atF==null?0.5:Math.min(1,Math.max(0,Number(atF)||0));
  let span=span0/(Number(f)||1);
  span=Math.min(D,Math.max(Math.min(mn,D),span));
  const s=Number(v0)+a*span0;          // время под курсором до зума
  return tlClampView(s-a*span,s-a*span+span,dur,mn);
}
// Окно показывает цель ЯВНОГО перехода (кнопка «играть кусок/вставку», двойной клик по вставке,
// выбор строки в списке): t внутри окна — окно не трогаем; t вне окна — окно встаёт так, чтобы t
// была на 10 % ширины от левого края. За плейхедом при игре, перемотке и протяжке по самому
// таймлайну окно НЕ ездит — решение владельца 2026-10-12 («пусть идёт, мы остаёмся на месте»).
// Окно шире записи или запись без длины — без сдвига. Окно не уходит за конец записи (tlClampView).
// Возвращает [v0,v1].
// ПОЧЕМУ 10 %: цель перехода встаёт у левого края — видно, что было перед ней, и что идёт дальше.
// Параметр зовётся tm, не t: одиночное t в static/app — функция перевода (сторож test_ui_static).
function tlReveal(v0,v1,tm,dur,minSpan){
  const span=Number(v1)-Number(v0);
  if(!(dur>0)||!(span>0)||span>=dur)return [v0,v1];
  if(tm>=v0&&tm<=v1)return [v0,v1];
  const a=tm-span*0.1;
  const r=tlClampView(a,a+span,dur,minSpan);
  return [r.v0,r.v1];
}

// Что захватывает курсор на таймлайне — один порядок для редактора шага 1 и для «Вся запись» подкаста:
// край куска > плейхед > линейка > середина куска. Кандидаты — [{kind:'edge'|'head'|'ruler'|'mid', dist, ...}],
// tol — допуски по видам (css px), у каждого таймлайна свои; вид без допуска в tol — без проверки
// (линейка и середина — это «внутри», их кандидат и так внутри). Выигрывает самый приоритетный вид
// в допуске; внутри вида — ближайший, при равенстве — первый в списке. Возвращает кандидата или null.
// ПОЧЕМУ так: решение владельца — один порядок на оба таймлайна, как был у подкаста. Кусок в 3 px и
// плейхед в 2 px — берётся край; плейхед выигрывает только тогда, когда края вне допуска.
const TL_HIT_ORDER=['edge','head','ruler','mid'];
function tlHitOrder(cands,tol){
  let best=null,rank=TL_HIT_ORDER.length;
  (cands||[]).forEach(c=>{
    const r=TL_HIT_ORDER.indexOf(c.kind);
    if(r<0)return;
    const lim=tol?tol[c.kind]:undefined;
    if(lim!==undefined&&!(c.dist<=lim))return;
    if(r<rank||(r===rank&&c.dist<best.dist)){best=c;rank=r;}
  });
  return best;
}

// Линейка полосы времени: подложка, нижняя черта, риски и подписи. Шаг — tlTickStep по ширине окна.
// o: {W,RH,dpr,v0,v1,total}; total — длина ВСЕЙ записи (от неё формат подписи, см. tlRulerLabel).
function tlPaintRuler(g,o){
  const C=TL_COL, W=o.W, RH=o.RH, dpr=o.dpr, v0=o.v0, v1=o.v1;
  g.fillStyle=C.rule;g.fillRect(0,0,W,RH);
  g.strokeStyle=C.ruleLine;g.lineWidth=1;g.beginPath();g.moveTo(0,RH+.5);g.lineTo(W,RH+.5);g.stroke();
  const st=tlTickStep(v1-v0,W/dpr);
  g.fillStyle=C.tickTx;g.font=(TL_RULER_PX*dpr)+'px ui-monospace,monospace';g.textBaseline='middle';
  for(let k=Math.ceil(v0/st);k*st<=v1+1e-6;k++){
    const tm=k*st, x=(tm-v0)/(v1-v0)*W;
    g.strokeStyle=C.tick;g.beginPath();g.moveTo(x+.5,0);g.lineTo(x+.5,RH);g.stroke();
    g.fillText(tlRulerLabel(tm,o.total,st),x+4*dpr,RH/2+dpr);
  }
}
// Рамка куска/блока от top до низа: заливка (o.on — выбран), обводка и две ручки краёв цветом o.edge.
// o.active — активный без выбора: обводка светлая. Подпись номера рисует вызывающий.
function tlDrawSpan(g,x0,x1,top,H,dpr,o){
  const C=TL_COL;
  g.fillStyle=o.on?C.selFill:C.blkFill;g.fillRect(x0,top,x1-x0,H-top);
  g.strokeStyle=(o.active&&!o.on)?C.active:o.edge;g.lineWidth=1;
  g.strokeRect(x0+.5,top+.5,x1-x0-1,H-top-1);
  g.fillStyle=o.edge;g.fillRect(x0,top,2*dpr,H-top);g.fillRect(x1-2*dpr,top,2*dpr,H-top);
}

// Плейхед на canvas: ОДНО рисование для окна подкаста и редактора нарезки. Рисуется ПОСЛЕДНИМ
// (после блоков и кусков). Сначала тёмная подложка — линия на 2 px шире и флажок на 1 px шире
// (#0d0d0d), потом синяя поверх: синий на синем блоке фото без обводки пропадал.
// x и h — в пикселях канваса (уже умноженные на dpr), dpr — devicePixelRatio.
function tlDrawPlayhead(ctx, x, h, dpr){
  const d = Number(dpr) > 0 ? Number(dpr) : 1;
  const lw = Math.max(1, 1.5 * d);
  const dark = TL_COL.bg;
  ctx.strokeStyle = dark; ctx.lineWidth = lw + 2 * d;
  ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  ctx.fillStyle = dark;
  ctx.beginPath(); ctx.moveTo(x - 5 * d, 0); ctx.lineTo(x + 5 * d, 0); ctx.lineTo(x, 7 * d); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = TL_COL.ph; ctx.lineWidth = lw;
  ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  ctx.fillStyle = TL_COL.ph;
  ctx.beginPath(); ctx.moveTo(x - 4 * d, 0); ctx.lineTo(x + 4 * d, 0); ctx.lineTo(x, 6 * d); ctx.closePath(); ctx.fill();
}

// Волна полосы времени: в каждом пикселе — МАКСИМУМ пиков на его отрезке времени. Одна точка на
// пиксель (так было у редактора) на отдалении пропускала громкие места: пиксель «вся запись» — это
// десятки секунд. o: {W,H,top,dpr,v0,v1,dur,peaks,pps,colorAt(s)}. colorAt — цвет в момент s; s растёт
// по x, поэтому вызывающий может держать указатель по сегментам. Пустые пики — волны нет вовсе.
function tlPaintWave(g,o){
  const pk=o.peaks||[], pps=o.pps, W=o.W, H=o.H, top=o.top, dpr=o.dpr, v0=o.v0, v1=o.v1, dur=o.dur||0;
  if(!pk.length)return;
  const mid=top+(H-top)/2, amp=(H-top)*0.44;
  let runColor=null;
  g.lineWidth=1;g.beginPath();
  for(let x=0;x<W;x++){
    const s=v0+x/W*(v1-v0), s2=v0+(x+1)/W*(v1-v0);
    if(s2<0||(dur>0&&s>dur))continue;
    const col=o.colorAt(s);
    if(col!==runColor){if(runColor)g.stroke();g.beginPath();g.strokeStyle=col;runColor=col;}
    let p=0;
    const i0=Math.max(0,Math.floor(s*pps)), i1=Math.min(pk.length,Math.max(i0+1,Math.floor(s2*pps)));
    for(let i=i0;i<i1;i++){const q=Number(pk[i])||0;if(q>p)p=q;}
    const h=Math.max(dpr,p*amp*2);
    g.moveTo(x+.5,mid-h/2);g.lineTo(x+.5,mid+h/2);
  }
  if(runColor)g.stroke();
}
// Колесо: горизонтальный жест или Shift — прокрутка окна, иначе — зум. Возвращает дельту
// прокрутки (по горизонтали — deltaX, иначе deltaY). Раньше у двух полос горизонтальное
// колесо уходило в зум: deltaY = 0 — и окно отдалялось.
function tlWheelIsPan(e){ return !!e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY); }
function tlWheelDelta(e){ return Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY; }
// Один щелчок колеса и кнопок «+»/«−» над полосами времени: множитель масштаба окна.
const TL_ZOOM_STEP = 1.35;
// Колесо на полосе времени — одна раскладка на три полосы: горизонтальный жест или Shift — протяжка
// вида, иначе — зум к курсору. Страницу не прокручиваем. pan(dxCss) — сдвиг в css px (вправо > 0);
// zoom(f, xCss) — множитель (больше 1 — ближе) и x курсора в css px от левого края полосы.
function tlWheelBind(el,o){
  el.addEventListener('wheel',e=>{
    e.preventDefault();
    if(tlWheelIsPan(e)){o.pan(tlWheelDelta(e));return;}
    if(!e.deltaY)return;
    const r=el.getBoundingClientRect();
    o.zoom(e.deltaY<0?TL_ZOOM_STEP:1/TL_ZOOM_STEP,e.clientX-r.left);
  },{passive:false});
}

// Протяжка вида: средняя кнопка — окно или лента едет за курсором на пройденное расстояние, из любой
// точки полосы. Общая для трёх таймлайнов. Левая кнопка на линейке — плейхед за курсором (решение
// владельца 2026-10-17), сюда не приходит: окно стоит, а прокрутка — средняя кнопка и колесо.
// el — элемент полосы; ev — событие нажатия. o.snap() — вид в момент нажатия; o.apply(dxCss, snap) —
// вернуть вид на смещение dxCss (css px, вправо > 0). Курсор — grabbing, класс panning — тоже на время
// тяги. Захват указателя — тяга не теряет вид за краем холста; pointercancel и потеря захвата кончают её.
// Пока el._tlPan — слежение за плейхедом вид не возвращает (tlPanning).
// Порог протяжки полосы времени (css px): пока указатель за всё нажатие не ушёл дальше этого от точки
// нажатия, вид стоит. Рука дрожит на 1–2 px, и без порога каждая тяга сдвигала бы окно на дрожь.
const TL_CLICK_MAX_PX=3;
function tlMidPan(el,ev,o){
  if(!el)return;
  tlMidGuard(el);
  const snap=o.snap?o.snap():null;
  const x0=ev.clientX, cur=el.style.cursor;
  let far=0;   // дальше всего от нажатия за всё движение: до порога окно не двигаем
  el.style.cursor='grabbing';el.classList.add('panning');el._tlPan=true;
  try{el.setPointerCapture(ev.pointerId);}catch(err){}
  const mv=m=>{ if(m.pointerId!==ev.pointerId) return; far=Math.max(far,Math.abs(m.clientX-x0));
    if(far>TL_CLICK_MAX_PX)o.apply(m.clientX-x0,snap); };
  const done=()=>{
    el.removeEventListener('pointermove',mv);
    el.removeEventListener('pointerup',onUp);
    el.removeEventListener('pointercancel',onCancel);
    el.removeEventListener('lostpointercapture',onCancel);
    el.style.cursor=cur;el.classList.remove('panning');el._tlPan=false;
    try{el.releasePointerCapture(ev.pointerId);}catch(err){} };
  const onUp=m=>{ if(m&&m.pointerId!==undefined&&m.pointerId!==ev.pointerId) return; done(); };
  const onCancel=m=>{ if(m&&m.pointerId!==undefined&&m.pointerId!==ev.pointerId) return; done(); };
  el.addEventListener('pointermove',mv);
  el.addEventListener('pointerup',onUp);
  el.addEventListener('pointercancel',onCancel);
  el.addEventListener('lostpointercapture',onCancel);
}
// Средняя кнопка не включает автоскролл браузера: гасим mousedown и auxclick (однократно на элемент).
// Ставится при инициализации полосы — mousedown уже прошёл бы к моменту старта тяги.
function tlMidGuard(el){
  if(!el||el._tlGuard)return;
  el._tlGuard=true;
  el.addEventListener('mousedown',e=>{if(e.button===1)e.preventDefault();});
  el.addEventListener('auxclick',e=>{if(e.button===1)e.preventDefault();});
}
function tlPanning(el){ return !!(el&&el._tlPan); }

// ================= отмена и повтор: одна механика на три таймлайна =================
// Редактор нарезки, «Вся запись» подкаста и вставки ведут свои стеки правок, а механика одна:
// снимок состояния — строка, сравниваемая с прошлой. Шаг пишет touch() ПОСЛЕ правки (если снимок
// сменился); undo/redo переносят текущий снимок в противоположный стек и возвращают прежний через
// apply(строка). rebase() — тихо сменить базу (нормализация сервера не должна стать шагом истории).
// Стеки живут внутри замыкания: снаружи только методы, ни `undo.length`, ни прямой записи.
function histStack(o){
  const max = o.max || 100;
  let undoS = [], redoS = [], last = null;
  const snap = () => o.snapshot();
  const ui = () => { if(o.onUi) o.onUi(); };
  function step(from, to){
    if(!from.length) return false;
    to.push(snap()); if(to.length > max) to.shift();
    o.apply(from.pop());
    last = snap(); ui();
    return true;
  }
  // Методы — стрелками, не сокращённой записью `name(){}`: сканер вызовов (tests/test_ui_js_calls.py)
  // принял бы такую запись за вызов без определения.
  return {
    touch: () => {
      const s = snap();
      if(last === null){ last = s; return false; }   // первой базы шага нет — только запоминаем
      if(s === last) return false;
      undoS.push(last); if(undoS.length > max) undoS.shift();
      redoS.length = 0;                               // новая правка убивает повтор
      last = s; ui();
      return true;
    },
    reset: () => { undoS = []; redoS = []; last = snap(); ui(); },
    rebase: () => { last = snap(); },
    undo: () => step(undoS, redoS),
    redo: () => step(redoS, undoS),
    canUndo: () => undoS.length > 0,
    canRedo: () => redoS.length > 0
  };
}
// Клавиши правки на полосах времени: резать — C, S (и русские «с», «ы»); удалить — D, «в», Delete,
// Backspace. Сочетания с Ctrl/⌘/Alt — не наши (Ctrl+C не режет). Вернёт 'split' | 'delete' | null.
function tlEditKey(e){
  if(e.ctrlKey || e.metaKey || e.altKey) return null;
  const k = (e.key || '').toLowerCase();
  if(k === 'c' || k === 'с' || k === 's' || k === 'ы') return 'split';
  if(k === 'd' || k === 'в' || e.key === 'Delete' || e.key === 'Backspace') return 'delete';
  return null;
}
// Клавиши: Ctrl/⌘+Z и «я» (русская раскладка) — отмена; Ctrl/⌘+Y, «н» и Ctrl/⌘+Shift+Z — повтор.
// Alt не считаем: это чужая комбинация. Вернёт 'undo' | 'redo' | null.
function histKey(e){
  if(!(e.ctrlKey || e.metaKey) || e.altKey) return null;
  const k = (e.key || '').toLowerCase();
  if(k === 'y' || k === 'н') return 'redo';
  if(k === 'z' || k === 'я') return e.shiftKey ? 'redo' : 'undo';
  return null;
}
function segUI(){document.querySelectorAll('.seg label').forEach(l=>l.classList.toggle('on',l.querySelector('input').checked));}
// Подсветку пилюли .seg переставляет ОДИН общий обработчик, а не каждый onchange. Давний баг
// «Камер»: выбрал 3 — число применилось, а подсветка осталась на 2, потому что обработчик
// (setMode) забыл segUI() в успешной ветке. Любой переключатель с забытым вызовом застревал
// так же. change всплывает от радио до document, поэтому срабатывает после inline-onchange.
// Программная смена .checked change не шлёт: такие места зовут segUI() сами (setMode, applyState).
document.addEventListener('change',e=>{if(e.target&&e.target.closest&&e.target.closest('.seg'))segUI();});
// Фото или видео решает ТОЛЬКО расширение (тип вставки определяет всё поведение в AE).
// Список один на весь фронт: он был скопирован в шести местах, и в пяти не было tiff —
// .tif считался фото при выборе файла, но в превью уезжал в <video> и кадр был пустой.
// Держать синхронно с IMG_EXT в core/insertlib.py.
function isPhotoPath(p){return /\.(png|jpe?g|webp|gif|bmp|avif|tiff?)$/i.test(String(p||''));}
// склонение по числу: plur(3,'пара','пары','пар') -> 'пары'
function plur(n,one,few,many){n=Math.abs(n)%100;const d=n%10;
  return (n>10&&n<20)?many:(d>1&&d<5)?few:(d===1)?one:many;}
// освободить медиа-элемент: пауза, снятый src (файл перестаёт держать соединение)
// и load() — им браузер отпускает ресурс, и убрать из DOM
function mediaFree(v){if(!v)return;
  try{v.pause();}catch(e){}
  try{v.removeAttribute('src');}catch(e){}
  try{v.load();}catch(e){}
  if(v.parentNode&&v.parentNode.removeChild)try{v.parentNode.removeChild(v);}catch(e){}}

