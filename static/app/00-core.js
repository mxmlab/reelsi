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
function segUI(){document.querySelectorAll('.seg label').forEach(l=>l.classList.toggle('on',l.querySelector('input').checked));}
// Фото или видео решает ТОЛЬКО расширение (тип вставки определяет всё поведение в AE).
// Список один на весь фронт: он был скопирован в шести местах, и в пяти не было tiff —
// .tif считался фото при выборе файла, но в превью уезжал в <video> и кадр был пустой.
// Держать синхронно с IMG_EXT в core/insertlib.py.
function isPhotoPath(p){return /\.(png|jpe?g|webp|gif|bmp|avif|tiff?)$/i.test(String(p||''));}
// склонение по числу: plur(3,'пара','пары','пар') -> 'пары'
function plur(n,one,few,many){n=Math.abs(n)%100;const d=n%10;
  return (n>10&&n<20)?many:(d>1&&d<5)?few:(d===1)?one:many;}
