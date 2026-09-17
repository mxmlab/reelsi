// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// Панель настроек стиля по схеме (Effect Controls)
// Загружается до static/app/95-styles.js. Порядок вызовов согласован.

let STSCHEMA = null;
// Промис идущей загрузки схемы: два открытия панели подряд не тянут /api/style_schema дважды.
let STSCHEMA_LOADING = null;
let SFX_PREFIX = {};
let SFX_ISVIDEO = {};

function initSfxMaps() {
  // V8: id кнопок — «st_» + ключ поля схемы. Здесь остались имена старой разметки
  // (st_transsfx, st_riserfile, st_trans) — по ним openSfxEdit не находил ни один
  // звук и брал префикс из самого id ('transsfx' вместо 'transition_sfx').
  SFX_PREFIX = {
    st_pop: 'pop',
    st_glitch: 'glitch'
  };
  SFX_ISVIDEO = {
    st_transition: true
  };
  if (!STSCHEMA || !STSCHEMA.layers) return;
  function walk(items) {
    for (const it of items) {
      if (it.type === 'field' && it.ctl === 'file') {
        const sfx = it.sfx || it.key;
        SFX_PREFIX['st_' + it.key] = sfx;
        if (it.video) SFX_ISVIDEO['st_' + it.key] = true;
      }
      if (it.items) walk(it.items);
    }
  }
  walk(STSCHEMA.layers);
}

async function loadStyleSchema() {
  // V11: одна неудачная загрузка раньше выключала панель до перезагрузки страницы —
  // renderStylePanel выходил по пустой схеме, и повторить попытку было некому.
  // Повтор — по следующему открытию панели (см. renderStylePanel), не циклом.
  if (STSCHEMA_LOADING) return STSCHEMA_LOADING;
  STSCHEMA_LOADING = (async () => {
    try {
      const res = await fetch('/api/style_schema');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      STSCHEMA = await res.json();
      initSfxMaps();
    } catch (e) {
      if (typeof uiLog === 'function') uiLog('loadStyleSchema: ' + e);
      if (typeof toast === 'function') toast(t('Не удалось загрузить схему стиля'));
      STSCHEMA = null;
    } finally {
      STSCHEMA_LOADING = null;
    }
  })();
  return STSCHEMA_LOADING;
}

// Таблица пересчётов conv (JA п. 2)
const stConv = {
  inv_pct: {
    toView: (v) => Math.round((1 - (v != null ? v : 0.5964)) * 100),
    toStore: (x) => {
      const n = parseFloat(x);
      if (isNaN(n)) return 0.5964;
      if (Math.round(n) === 40) return 0.5964;
      return Math.min(0.98, Math.max(0.05, 1 - n / 100));
    }
  },
  pct_h: {
    toView: (v, H) => {
      const h = H || (typeof IPV !== 'undefined' && IPV.plan && IPV.plan.h) || (typeof styleFrameDim === 'function' ? styleFrameDim().h : 1920);
      const pct = Math.round(((v != null ? v : 0) / h) * 100);
      return Math.max(-50, Math.min(100, pct));
    },
    toStore: (x, H) => {
      const h = H || (typeof IPV !== 'undefined' && IPV.plan && IPV.plan.h) || (typeof styleFrameDim === 'function' ? styleFrameDim().h : 1920);
      const n = parseFloat(x);
      return isNaN(n) ? 0 : Math.round((n / 100) * h);
    }
  },
  pct_fx: {
    toView: (v, W) => {
      const w = W || (typeof styleFrameDim === 'function' ? styleFrameDim().w : ((typeof IPV !== 'undefined' && IPV.plan && IPV.plan.w) || 1080));
      return +(((v || 0) / w) * 100).toFixed(1);
    },
    toStore: (x, W) => {
      const w = W || (typeof styleFrameDim === 'function' ? styleFrameDim().w : ((typeof IPV !== 'undefined' && IPV.plan && IPV.plan.w) || 1080));
      return Math.round(((parseFloat(x) || 0) / 100) * w);
    }
  },
  pct_fy: {
    toView: (v, H) => {
      const h = H || (typeof styleFrameDim === 'function' ? styleFrameDim().h : ((typeof IPV !== 'undefined' && IPV.plan && IPV.plan.h) || 1920));
      return +(((v || 0) / h) * 100).toFixed(1);
    },
    toStore: (x, H) => {
      const h = H || (typeof styleFrameDim === 'function' ? styleFrameDim().h : ((typeof IPV !== 'undefined' && IPV.plan && IPV.plan.h) || 1920));
      return Math.round(((parseFloat(x) || 0) / 100) * h);
    }
  },
  frac_pct: {
    toView: (v) => Math.round(((v != null ? v : 0)) * 1000) / 10,
    toStore: (x) => {
      const n = parseFloat(x);
      return isNaN(n) ? 0 : Math.round(n * 10) / 1000;
    }
  },
  frac_pct_int: {
    toView: (v) => Math.round(((v != null ? v : 0)) * 100),
    toStore: (x) => {
      const n = parseFloat(x);
      return isNaN(n) ? 0 : Math.round(n) / 100;
    }
  }
};

function stRgb2hex(a) {
  if (typeof rgb2hex === 'function') return rgb2hex(a);
  const c = x => ('0' + Math.round(Math.max(0, Math.min(1, x || 0)) * 255).toString(16)).slice(-2);
  a = a || [1, 0.9176, 0];
  return '#' + c(a[0]) + c(a[1]) + c(a[2]);
}

function stView(field, stored) {
  if (!field) return stored != null ? stored : '';
  // Общее правило: undefined (ключа нет) и null у НЕ-nullable поля → BASE.
  // У nullable-поля null остаётся «пусто».
  const base = (STSCHEMA && STSCHEMA.base) || {};
  const baseDef = base[field.key];
  const missing = (stored === undefined || (stored === null && !field.nullable));

  if (field.conv && stConv[field.conv]) {
    return stConv[field.conv].toView(missing ? baseDef : stored);
  }
  const ctl = field.ctl;
  if (ctl === 'bool') {
    return Boolean(missing ? baseDef : stored);
  }
  if (ctl === 'color') {
    const v = missing ? baseDef : stored;
    if (Array.isArray(v)) return stRgb2hex(v).toUpperCase();
    if (typeof v === 'string') return v.toUpperCase();
    return '#FFFFFF';
  }
  if (ctl === 'color_opt') {
    if (!stored || !Array.isArray(stored)) return '';
    return stRgb2hex(stored).toUpperCase();
  }
  if (ctl === 'num' || ctl === 'int' || ctl === 'angle') {
    if (stored == null) {
      // Пусто у nullable-поля — это «авто» (как пустой input с placeholder в старой
      // разметке): stStore('') вернёт null, и ключ не заведётся сам собой.
      if (field.nullable) return '';
      return baseDef != null ? baseDef : 0;
    }
    return stored;
  }
  if (ctl === 'point') {
    if (stored === undefined) return baseDef != null ? baseDef : 0.5;
    return stored != null ? stored : 0.5;
  }
  if (ctl === 'layer_order') {
    if (Array.isArray(stored)) return [...stored];
    const def = base.layer_order || ['subs', 'video', 'roto', 'photo', 'intro'];
    return [...def];
  }
  if (ctl === 'select' || ctl === 'font') {
    if (missing && baseDef != null) return baseDef;
  }
  if (field.key === 'disclaimer') {
    return stored != null ? stored : '';
  }
  return stored != null ? stored : '';
}

function stStore(field, view, opt) {
  if (!field) return view;
  if (field.conv && stConv[field.conv]) {
    return stConv[field.conv].toStore(view);
  }
  const ctl = field.ctl;
  const baseDef = (STSCHEMA && STSCHEMA.base && STSCHEMA.base[field.key]);

  if (ctl === 'bool') {
    return Boolean(view);
  }
  if (ctl === 'color') {
    if (Array.isArray(view)) return view;
    let hex = (view || '').trim();
    if (!hex) return baseDef || [1, 1, 1];
    if (hex[0] !== '#') hex = '#' + hex;
    if (baseDef && Array.isArray(baseDef) && stRgb2hex(baseDef).toLowerCase() === hex.toLowerCase()) {
      return baseDef;
    }
    if (opt && opt.orig && Array.isArray(opt.orig) && stRgb2hex(opt.orig).toLowerCase() === hex.toLowerCase()) {
      return opt.orig;
    }
    const r = parseInt(hex.slice(1, 3), 16) || 0;
    const g = parseInt(hex.slice(3, 5), 16) || 0;
    const b = parseInt(hex.slice(5, 7), 16) || 0;
    return [
      Math.round((r / 255) * 10000) / 10000,
      Math.round((g / 255) * 10000) / 10000,
      Math.round((b / 255) * 10000) / 10000
    ];
  }
  if (ctl === 'color_opt') {
    if (!view || (typeof view === 'string' && !view.trim())) return null;
    let hex = view.trim();
    if (hex[0] !== '#') hex = '#' + hex;
    if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return null;
    if (baseDef && Array.isArray(baseDef) && stRgb2hex(baseDef).toLowerCase() === hex.toLowerCase()) {
      return baseDef;
    }
    if (opt && opt.orig && Array.isArray(opt.orig) && stRgb2hex(opt.orig).toLowerCase() === hex.toLowerCase()) {
      return opt.orig;
    }
    const r = parseInt(hex.slice(1, 3), 16) || 0;
    const g = parseInt(hex.slice(3, 5), 16) || 0;
    const b = parseInt(hex.slice(5, 7), 16) || 0;
    return [
      Math.round((r / 255) * 10000) / 10000,
      Math.round((g / 255) * 10000) / 10000,
      Math.round((b / 255) * 10000) / 10000
    ];
  }
  if (ctl === 'num' || ctl === 'angle' || ctl === 'point') {
    const num = parseFloat(view);
    if (isNaN(num)) {
      if (field.nullable) return null;
      return baseDef != null ? baseDef : 0;
    }
    if (field.positive && num <= 0) {
      return baseDef != null ? baseDef : 0;
    }
    return num;
  }
  if (ctl === 'int') {
    const num = parseInt(view, 10);
    if (isNaN(num)) {
      if (field.nullable) return null;
      return baseDef != null ? baseDef : 0;
    }
    if (field.positive && num <= 0) {
      return baseDef != null ? baseDef : 1;
    }
    return num;
  }
  if (field.key === 'hl_font') {
    if (opt && opt.hl_bold === false) return null;
    if (!view || !String(view).trim()) return null;
    return String(view).trim();
  }
  if (field.key === 'disclaimer') {
    if (opt && opt.disclaimer_show === false) return '';
    // Пустая строка '' = «дисклеймер скрыт» (так пишет stEdit при выключенном тумблере).
    // null = «показать текст по умолчанию». Не путать!
    if (view === '') return '';
    if (typeof view === 'string') return view.trim() || null;
    return view || null;
  }
  if (ctl === 'layer_order') {
    if (Array.isArray(view)) return [...view];
    return baseDef || ['subs', 'video', 'roto', 'photo', 'intro'];
  }
  // select / font НЕ-nullable: пустое → BASE
  if ((ctl === 'select' || ctl === 'font') && !field.nullable) {
    if (!view || !String(view).trim()) {
      return baseDef != null ? baseDef : '';
    }
  }
  if (field.nullable && (!view || !String(view).trim())) return null;
  return view != null ? String(view) : '';
}

function findFieldByKey(key) {
  if (!STSCHEMA || !STSCHEMA.layers) return null;
  function walk(items) {
    for (const it of items) {
      if (it.type === 'field' && (it.key === key || it.key2 === key)) return it;
      if (it.items) {
        const res = walk(it.items);
        if (res) return res;
      }
    }
    return null;
  }
  return walk(STSCHEMA.layers);
}

// Показанное (view) значение поля из DOM панели — общая дверь для внешних подсказок
// (маска рото, rotoSync): id полей строятся из схемы, отдельных «своих» id у них нет.
// null — поля нет в панели (или значение не число), и вызывающий решает, что делать.
function stReadView(key) {
  const field = findFieldByKey(key);
  if (!field) {
    // key бывает не полем, а ТУМБЛЕРОМ слоя или группы (roto, sub_bg, caption…):
    // у него та же галка st_<key>, но в списке полей схемы его нет.
    const chk = document.getElementById('st_' + key);
    return (chk && typeof chk.checked === 'boolean') ? !!chk.checked : null;
  }
  if (field.ctl === 'bool') {
    const el = document.getElementById('st_' + key);
    return el ? !!el.checked : null;
  }
  if (field.ctl === 'num' || field.ctl === 'int' || field.ctl === 'angle') {
    const span = document.getElementById('st_' + key + '_val');
    const inp = document.getElementById('st_' + key + '_input');
    const raw = (span && span.style.display !== 'none') ? span.textContent : (inp ? inp.value : '');
    const n = parseFloat(raw);
    return isNaN(n) ? null : n;
  }
  if (field.ctl === 'color' || field.ctl === 'color_opt') {
    const hex = document.getElementById('st_' + key + '_hex');
    return hex ? hex.value : null;
  }
  if (field.ctl === 'layer_order') {
    const s = CURSTYLE || {};
    return Array.isArray(s[key]) ? [...s[key]] : null;
  }
  if (field.ctl === 'textarea') {
    // У дисклеймера свой id (st_disc_text) — так же читают его fillStyleFields,
    // stEdit и stRefresh. Без этой ветки stReadView отдавал value ЧЕКБОКСА слоя
    // st_disclaimer: у textarea-поля двери показывали и читали разные элементы.
    const ta = document.getElementById(field.key === 'disclaimer' ? 'st_disc_text' : ('st_' + key));
    return ta ? ta.value : null;
  }
  const el = document.getElementById('st_' + key);
  return el ? el.value : null;
}

function isExpanded(id) {
  try {
    const v = localStorage.getItem('reelsi_sttw_' + id);
    if (v !== null) return v === '1';
  } catch (e) {}
  return id === 'subs' || id === 'subs.text';
}

function toggleExpanded(id) {
  const next = !isExpanded(id);
  try {
    localStorage.setItem('reelsi_sttw_' + id, next ? '1' : '0');
  } catch (e) {}
  return next;
}

function getStyleParent() {
  let orig = (typeof STYLE_EDIT_ORIG !== 'undefined' && STYLE_EDIT_ORIG) ? STYLE_EDIT_ORIG : null;
  if (!orig) {
    const k = (typeof STYLE_EDITING !== 'undefined' && STYLE_EDITING) || (typeof val === 'function' && val('style'));
    orig = (k && k !== '__custom__' && k !== '__edit__' && typeof STYLES !== 'undefined' && STYLES[k])
      ? STYLES[k]
      : ((typeof STYLES !== 'undefined' && STYLES.base) || (STSCHEMA && STSCHEMA.base) || {});
  }
  return orig;
}

function isNodeOn(item) {
  if (!item || !item.toggle) return true;
  const s = CURSTYLE || {};
  if (item.toggle === 'disclaimer') {
    return s.disclaimer !== '';
  }
  const baseDef = (STSCHEMA && STSCHEMA.base) ? STSCHEMA.base[item.toggle] : false;
  const val = (s[item.toggle] !== undefined && s[item.toggle] !== null) ? s[item.toggle] : baseDef;
  return Boolean(val);
}

function renderStylePanel() {
  const host = document.getElementById('stpanel');
  if (!host) return;
  if (!STSCHEMA || !STSCHEMA.layers) {
    // V11: схема не загрузилась в прошлый раз. Пробуем ещё раз — по действию человека
    // (открыл панель), а не циклом: не выйдет — следующее открытие попробует снова.
    loadStyleSchema().then(() => { if (STSCHEMA && STSCHEMA.layers) renderStylePanel(); });
    return;
  }
  host.innerHTML = '';

  // V5: алиас tr убран — вызываем t напрямую, чтобы сторож перевода видел строки

  function renderItems(items, level) {
    const frag = document.createDocumentFragment();
    for (const item of items) {
      if (item.type === 'group') {
        const gBox = document.createElement('div');
        gBox.className = 'stgroup-box';
        gBox.dataset.group = item.id;

        const open = isExpanded(item.id);
        const gRow = document.createElement('div');
        gRow.className = 'strow stgroup';
        gRow.tabIndex = 0;
        gRow.setAttribute('role', 'treeitem');
        gRow.setAttribute('aria-label', t(item.label || item.id));
        gRow.dataset.tw = item.id;
        gRow.style.setProperty('--lvl', level);

        // col 1: обёртка — треугольник + fx-бэйдж + галка
        const ctl1 = document.createElement('span');
        ctl1.className = 'strow-ctl';
        ctl1.style.display = 'inline-flex';
        ctl1.style.alignItems = 'center';
        ctl1.style.gap = '4px';

        const tw = document.createElement('span');
        tw.className = 'sttw' + (open ? ' open' : '');
        tw.setAttribute('aria-expanded', open ? 'true' : 'false');
        tw.textContent = '';
        ctl1.appendChild(tw);

        if (item.fx) {
          const fx = document.createElement('span');
          fx.className = 'stfx';
          fx.textContent = 'fx';
          ctl1.appendChild(fx);
        }

        if (item.toggle) {
          const tch = document.createElement('input');
          tch.type = 'checkbox';
          tch.id = 'st_' + item.toggle;
          tch.className = 'stchk';
          tch.setAttribute('aria-label', t(item.label || item.id));
          tch.onchange = (e) => {
            e.stopPropagation();
            stToggleGroup(item.id, item.toggle, tch.checked);
          };
          ctl1.appendChild(tch);
        }
        gRow.appendChild(ctl1);

        // col 2: точка изменения (фиксированная колонка 8px СЛЕВА от имени)
        const dot = document.createElement('span');
        dot.className = 'stdot';
        dot.title = t('Изменено');
        dot.dataset.dotGroup = item.id;
        gRow.appendChild(dot);

        // col 3: имя группы
        const lbl = document.createElement('span');
        lbl.className = 'stlabel';
        lbl.textContent = t(item.label || item.id);
        gRow.appendChild(lbl);

        // col 4: кнопка сброса в колонке значений (180px)
        const rst = document.createElement('button');
        rst.type = 'button';
        rst.className = 'streset';
        rst.textContent = t('Сброс');
        rst.setAttribute('aria-label', t('Сброс'));
        rst.onclick = (e) => {
          e.stopPropagation();
          stReset(item.id);
        };
        gRow.appendChild(rst);

        const safeId = item.id.replace(/\./g, '_');
        const gBody = document.createElement('div');
        gBody.className = 'stbody stbody-lvl' + (level + 1);
        gBody.id = 'stbody_' + safeId;
        gBody.style.display = (open && isNodeOn(item)) ? '' : 'none';

        if (item.items) {
          gBody.appendChild(renderItems(item.items, level + 1));
        }

        const toggleGroup = () => {
          const n = toggleExpanded(item.id);
          tw.classList.toggle('open', n);
          tw.setAttribute('aria-expanded', n ? 'true' : 'false');
          gBody.style.display = (n && isNodeOn(item)) ? '' : 'none';
        };

        gRow.onclick = (e) => {
          if (e.target.closest('.stchk') || e.target.closest('.chk') || e.target.closest('button')) return;
          toggleGroup();
        };
        gRow.onkeydown = (e) => {
          if (e.key === 'ArrowRight') {
            if (!isExpanded(item.id)) toggleGroup();
          } else if (e.key === 'ArrowLeft') {
            if (isExpanded(item.id)) toggleGroup();
          } else if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggleGroup();
          }
        };

        gBox.appendChild(gRow);
        gBox.appendChild(gBody);
        frag.appendChild(gBox);
      } else if (item.type === 'field') {
        const fRow = document.createElement('div');
        fRow.className = 'strow stfield';
        fRow.id = 'strow_' + item.key;
        fRow.style.setProperty('--lvl', level);

        // col 1: controls (треугольник числа или спейсер)
        const ctl1 = document.createElement('span');
        ctl1.className = 'strow-ctl';
        ctl1.style.display = 'inline-flex';
        ctl1.style.alignItems = 'center';
        ctl1.style.gap = '4px';

        const isNumCtl = (item.ctl === 'num' || item.ctl === 'int' || item.ctl === 'angle');
        let twField = null;
        if (isNumCtl) {
          const fOpen = isExpanded('field_' + item.key);
          twField = document.createElement('span');
          twField.className = 'sttw sttw-field' + (fOpen ? ' open' : '');
          twField.setAttribute('role', 'button');
          twField.tabIndex = 0;
          twField.setAttribute('aria-label', fOpen ? t('Свернуть') : t('Развернуть'));
          twField.setAttribute('aria-expanded', fOpen ? 'true' : 'false');
          twField.dataset.tw = 'field_' + item.key;
          twField.textContent = '';
          ctl1.appendChild(twField);
        } else {
          const spc = document.createElement('span');
          spc.className = 'sttw-spacer';
          ctl1.appendChild(spc);
        }
        fRow.appendChild(ctl1);

        // col 2: точка изменения (фиксированная колонка 8px СЛЕВА от имени)
        const dot = document.createElement('span');
        dot.className = 'stdot';
        dot.title = t('Сброс');
        dot.setAttribute('role', 'button');
        dot.tabIndex = 0;
        dot.setAttribute('aria-label', t('Сброс'));
        dot.dataset.dotKey = item.key;
        dot.onclick = (e) => {
          if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
          stResetKey(item.key);
        };
        dot.onkeydown = (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            if (e.preventDefault) e.preventDefault();
            if (e.stopPropagation) e.stopPropagation();
            stResetKey(item.key);
          }
        };
        fRow.appendChild(dot);

        // col 3: имя поля (1fr, ellipsis + тултип !)
        const nameWrap = document.createElement('div');
        nameWrap.className = 'stfield-name';

        const fTitle = t(item.label || item.key);
        const fLbl = document.createElement('label');
        fLbl.className = 'stfield-lbl';
        if (isNumCtl) {
          fLbl.htmlFor = 'st_' + item.key + '_val';
        } else if (item.ctl === 'color' || item.ctl === 'color_opt') {
          fLbl.htmlFor = 'st_' + item.key + '_hex';
        } else if (item.ctl === 'point') {
          fLbl.htmlFor = 'st_pickzoom';
        } else if (item.key === 'layer_order') {
          fLbl.id = 'st_lbl_layer_order';
        } else if (item.ctl === 'textarea') {
          fLbl.htmlFor = item.key === 'disclaimer' ? 'st_disc_text' : ('st_' + item.key);
        } else {
          fLbl.htmlFor = 'st_' + item.key;
        }
        fLbl.textContent = fTitle;
        nameWrap.appendChild(fLbl);

        if (item.tip) {
          const tip = document.createElement('span');
          tip.className = 'i';
          tip.dataset.t = t(item.tip);
          tip.textContent = '!';
          nameWrap.appendChild(tip);
        }
        fRow.appendChild(nameWrap);

        const right = document.createElement('div');
        right.className = 'stfield-right';

        if (isNumCtl) {
          const span = document.createElement('span');
          span.className = 'stnum-val';
          span.id = 'st_' + item.key + '_val';
          span.dataset.key = item.key;
          span.tabIndex = 0;
          span.setAttribute('role', 'spinbutton');
          span.setAttribute('aria-label', fTitle);
          const limMin = item.lim_min != null ? item.lim_min : (item.min != null ? item.min : null);
          const limMax = item.lim_max != null ? item.lim_max : (item.max != null ? item.max : null);
          if (limMin != null) span.setAttribute('aria-valuemin', limMin);
          if (limMax != null) span.setAttribute('aria-valuemax', limMax);
          span.setAttribute('aria-valuenow', '0');
          span.textContent = '0';

          const edit = document.createElement('input');
          edit.type = 'text';
          edit.className = 'stnum-edit';
          edit.id = 'st_' + item.key + '_input';
          edit.dataset.key = item.key;
          edit.style.display = 'none';
          edit.setAttribute('aria-label', fTitle);

          right.appendChild(span);
          right.appendChild(edit);
          initNumDrag(span, edit, item);
        } else if (item.ctl === 'bool') {
          const chk = document.createElement('input');
          chk.type = 'checkbox';
          chk.id = 'st_' + item.key;
          chk.className = 'stchk';
          chk.onchange = () => stEdit();
          right.appendChild(chk);
        } else if (item.ctl === 'color' || item.ctl === 'color_opt') {
          const cWrap = document.createElement('div');
          cWrap.className = 'stcolor-wrap';

          const swatch = document.createElement('input');
          swatch.type = 'color';
          swatch.id = 'st_' + item.key + '_color';
          swatch.className = 'stcolor-swatch';
          swatch.setAttribute('aria-label', fTitle);
          swatch.onchange = () => stColorSwatchChange(item.key, swatch.value);

          const hex = document.createElement('input');
          hex.type = 'text';
          hex.id = 'st_' + item.key + '_hex';
          hex.className = 'sthex';
          hex.maxLength = 7;
          hex.placeholder = item.ctl === 'color_opt' ? '' : '#FFFFFF';
          hex.onchange = () => stHexChange(item.key, hex.value);
          hex.oninput = () => stHexInput(item.key, hex.value);

          cWrap.appendChild(swatch);
          cWrap.appendChild(hex);
          right.appendChild(cWrap);
        } else if (item.ctl === 'select') {
          const sel = document.createElement('select');
          sel.id = 'st_' + item.key;
          sel.className = 'stselect';
          sel.onchange = () => stEdit();
          if (item.options) {
            for (const opt of item.options) {
              const o = document.createElement('option');
              o.value = opt[0];
              o.textContent = t(opt[1]);
              sel.appendChild(o);
            }
          }
          right.appendChild(sel);
        } else if (item.ctl === 'font') {
          const inp = document.createElement('input');
          inp.type = 'text';
          inp.id = 'st_' + item.key;
          inp.className = 'stfont';
          inp.setAttribute('list', item.list || 'fontlist');
          if (item.placeholder) inp.placeholder = t(item.placeholder);
          inp.onchange = () => stEdit();
          inp.oninput = () => stEdit();
          right.appendChild(inp);
        } else if (item.ctl === 'file') {
          const fWrap = document.createElement('div');
          fWrap.className = 'stfile-wrap';

          const inp = document.createElement('input');
          inp.type = 'text';
          inp.id = 'st_' + item.key;
          inp.className = 'stfile';
          if (item.placeholder) inp.placeholder = t(item.placeholder);
          inp.onchange = () => stEdit();

          const btnPick = document.createElement('button');
          btnPick.type = 'button';
          btnPick.className = 'sm';
          btnPick.textContent = t('Файл…');
          btnPick.setAttribute('aria-label', fTitle + ' — ' + t('Файл…'));
          btnPick.onclick = () => {
            if (typeof pickInto === 'function') pickInto('st_' + item.key);
          };

          const btnEdit = document.createElement('button');
          btnEdit.type = 'button';
          btnEdit.className = 'icon';
          btnEdit.setAttribute('aria-label', t('Настройка звука'));
          btnEdit.innerHTML = '<span data-ic="pencil"></span>';
          btnEdit.onclick = () => {
            if (typeof openSfxEdit === 'function') openSfxEdit('st_' + item.key);
          };

          fWrap.appendChild(inp);
          fWrap.appendChild(btnPick);
          fWrap.appendChild(btnEdit);
          right.appendChild(fWrap);
        } else if (item.ctl === 'point') {
          const ptWrap = document.createElement('div');
          ptWrap.className = 'stpoint-wrap';

          const ptVal = document.createElement('span');
          ptVal.className = 'stpoint-val';
          ptVal.id = 'st_' + item.key + '_val';
          ptVal.textContent = '0.50, 0.50';

          const btnPick = document.createElement('button');
          btnPick.type = 'button';
          btnPick.className = 'sm';
          btnPick.id = 'st_pickzoom';
          btnPick.textContent = t('Прицел');
          btnPick.setAttribute('aria-label', fTitle);
          btnPick.onclick = () => {
            if (typeof pickZoomPoint === 'function') pickZoomPoint();
          };

          ptWrap.appendChild(ptVal);
          ptWrap.appendChild(btnPick);
          right.appendChild(ptWrap);
        } else if (item.ctl === 'textarea') {
          const ta = document.createElement('textarea');
          ta.id = item.key === 'disclaimer' ? 'st_disc_text' : ('st_' + item.key);
          ta.className = 'sttextarea';
          ta.rows = 3;
          if (item.placeholder) ta.placeholder = t(item.placeholder);
          ta.onchange = () => stEdit();
          ta.oninput = () => stEdit();
          right.appendChild(ta);
        }

        fRow.appendChild(right);
        frag.appendChild(fRow);

        // V7: у layer_order раньше была голая коробка списка — без метки, точки
        // «изменено» и сброса. Виджет порядка остаётся как есть (renderLayerOrderUI
        // ищет #st_layer_order_list), но идёт отдельной строкой: в колонку значений
        // (180px) он не влезает — так же, как ползунок у числового поля.
        if (item.ctl === 'layer_order') {
          const loBox = document.createElement('div');
          loBox.id = 'st_layer_order_list';
          loBox.className = 'layer-order-list';
          loBox.setAttribute('role', 'list');
          loBox.setAttribute('aria-labelledby', 'st_lbl_layer_order');
          loBox.style.setProperty('--lvl', level);
          frag.appendChild(loBox);
          continue;   // виджет порядка — вся обвязка поля, дальше ручек у него нет
        }

        if (isNumCtl) {
          const fOpen = isExpanded('field_' + item.key);
          const sldRow = document.createElement('div');
          sldRow.className = 'strow stslider-row';
          sldRow.id = 'stslider_row_' + item.key;
          sldRow.style.setProperty('--lvl', level);
          sldRow.style.display = fOpen ? '' : 'none';

          const sldWrap = document.createElement('div');
          sldWrap.className = 'stslider-inner';

          const range = document.createElement('input');
          range.type = 'range';
          range.id = 'st_' + item.key + '_slider';
          range.className = 'stslider';
          range.setAttribute('aria-label', fTitle);
          range.min = item.min != null ? item.min : 0;
          range.max = item.max != null ? item.max : 100;
          range.step = item.step != null ? item.step : (item.ctl === 'int' ? 1 : 0.1);
          range.oninput = () => stSliderInput(item.key, range.value);

          if (item.hint === 'rotomask') {
            range.onfocus = () => { if (typeof rotoMaskSync === 'function') rotoMaskSync(); };
            range.onblur = () => { if (typeof rotoMaskHide === 'function') rotoMaskHide(); };
          }

          sldWrap.appendChild(range);

          if (item.ctl === 'angle') {
            const dial = createAngleDial(item.key, item);
            sldWrap.appendChild(dial);
          }

          sldRow.appendChild(sldWrap);
          frag.appendChild(sldRow);

          if (twField) {
            const toggleTw = (e) => {
              if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
              if (e && typeof e.preventDefault === 'function') e.preventDefault();
              const n = toggleExpanded('field_' + item.key);
              twField.classList.toggle('open', n);
              twField.setAttribute('aria-expanded', n ? 'true' : 'false');
              twField.setAttribute('aria-label', n ? t('Свернуть') : t('Развернуть'));
              sldRow.style.display = n ? '' : 'none';
            };
            twField.onclick = toggleTw;
            twField.onkeydown = (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                toggleTw(e);
              }
            };
          }
        }
      }
    }
    return frag;
  }

  for (const layer of STSCHEMA.layers) {
    const lBox = document.createElement('div');
    lBox.className = 'stlayer-box';
    lBox.dataset.layer = layer.id;

    const open = isExpanded(layer.id);
    const lRow = document.createElement('div');
    lRow.className = 'strow stlayer';
    lRow.tabIndex = 0;
    lRow.setAttribute('role', 'treeitem');
    lRow.setAttribute('aria-label', t(layer.label || layer.id));
    lRow.dataset.tw = layer.id;

    // col 1: обёртка — треугольник + галка (или спейсер)
    const ctl1 = document.createElement('span');
    ctl1.className = 'strow-ctl';
    ctl1.style.display = 'inline-flex';
    ctl1.style.alignItems = 'center';
    ctl1.style.gap = '4px';

    const tw = document.createElement('span');
    tw.className = 'sttw' + (open ? ' open' : '');
    tw.setAttribute('aria-expanded', open ? 'true' : 'false');
    tw.textContent = '';
    ctl1.appendChild(tw);

    if (layer.toggle) {
      const tch = document.createElement('input');
      tch.type = 'checkbox';
      tch.id = 'st_' + layer.toggle;
      tch.className = 'stchk';
      tch.setAttribute('aria-label', t(layer.label || layer.id));
      tch.onchange = (e) => {
        e.stopPropagation();
        stToggleLayer(layer.id, layer.toggle, tch.checked);
      };
      ctl1.appendChild(tch);
    } else {
      const chkSpc = document.createElement('span');
      chkSpc.className = 'stchk-spacer';
      ctl1.appendChild(chkSpc);
    }
    lRow.appendChild(ctl1);

    // col 2: точка изменения (фиксированная колонка 8px СЛЕВА от имени)
    const dot = document.createElement('span');
    dot.className = 'stdot';
    dot.title = t('Изменено');
    dot.dataset.dotLayer = layer.id;
    lRow.appendChild(dot);

    // col 3: имя слоя
    const lbl = document.createElement('span');
    lbl.className = 'stlabel';
    lbl.textContent = t(layer.label || layer.id);
    lRow.appendChild(lbl);

    // col 4: пустая ячейка для колонки значений
    const valSpc = document.createElement('span');
    valSpc.className = 'stval-spacer';
    lRow.appendChild(valSpc);

    const lBody = document.createElement('div');
    lBody.className = 'stbody stbody-lvl1';
    lBody.id = 'stbody_' + layer.id;
    lBody.style.display = (open && isNodeOn(layer)) ? '' : 'none';

    if (layer.items) {
      lBody.appendChild(renderItems(layer.items, 1));
    }

    const toggleLayer = () => {
      const n = toggleExpanded(layer.id);
      tw.classList.toggle('open', n);
      tw.setAttribute('aria-expanded', n ? 'true' : 'false');
      lBody.style.display = (n && isNodeOn(layer)) ? '' : 'none';
    };

    lRow.onclick = (e) => {
      if (e.target.closest('.stchk') || e.target.closest('.chk') || e.target.closest('button')) return;
      toggleLayer();
    };
    lRow.onkeydown = (e) => {
      if (e.key === 'ArrowRight') {
        if (!isExpanded(layer.id)) toggleLayer();
      } else if (e.key === 'ArrowLeft') {
        if (isExpanded(layer.id)) toggleLayer();
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggleLayer();
      }
    };

    lBox.appendChild(lRow);
    lBox.appendChild(lBody);
    host.appendChild(lBox);
  }

  if (typeof renderLayerOrderUI === 'function') {
    renderLayerOrderUI();
  }

  fillStyleFields();
}

function initNumDrag(span, input, field) {
  const step = field.step || (field.ctl === 'int' ? 1 : 0.1);
  const limMin = field.lim_min != null ? field.lim_min : (field.min != null ? field.min : -Infinity);
  const limMax = field.lim_max != null ? field.lim_max : (field.max != null ? field.max : Infinity);

  const openEdit = () => {
    span.style.display = 'none';
    input.style.display = '';
    input.value = span.textContent;
    input.focus();
    input.select();
  };

  const cancelEdit = () => {
    input.value = span.textContent;
    input.style.display = 'none';
    span.style.display = '';
    span.focus();
  };

  const commitInput = () => {
    input.style.display = 'none';
    span.style.display = '';
    const v = parseFloat(input.value);
    if (!isNaN(v)) {
      const fin = (field.ctl === 'int') ? Math.round(v) : v;
      span.textContent = fin;
      span.setAttribute('aria-valuenow', fin);
      const slider = document.getElementById('st_' + field.key + '_slider');
      if (slider) slider.value = Math.max(field.min != null ? field.min : -Infinity, Math.min(field.max != null ? field.max : Infinity, fin));
      if (field.ctl === 'angle') updateAngleDial(field.key, fin);
      stEdit();
    }
  };

  span.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === 'F2') {
      if (e.preventDefault) e.preventDefault();
      openEdit();
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      if (e.preventDefault) e.preventDefault();
      const mult = e.shiftKey ? 10 : 1;
      const delta = (e.key === 'ArrowUp' ? 1 : -1) * step * mult;
      const viewVal = parseFloat(span.textContent) || 0;
      let v = viewVal + delta;
      if (field.ctl === 'int') {
        v = Math.round(v);
      } else {
        const dec = (step.toString().split('.')[1] || '').length;
        v = parseFloat(v.toFixed(Math.max(dec, 1)));
      }
      const minB = Math.min(viewVal, limMin);
      const maxB = Math.max(viewVal, limMax);
      v = Math.max(minB, Math.min(maxB, v));
      span.textContent = v;
      span.setAttribute('aria-valuenow', v);
      input.value = v;
      const slider = document.getElementById('st_' + field.key + '_slider');
      if (slider) slider.value = Math.max(field.min != null ? field.min : -Infinity, Math.min(field.max != null ? field.max : Infinity, v));
      if (field.ctl === 'angle') updateAngleDial(field.key, v);
      stEdit();
      if (field.hint === 'rotomask' && typeof rotoMaskSync === 'function') rotoMaskSync();
    }
  });

  span.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    if (e.preventDefault) e.preventDefault();
    const startX = e.clientX;
    const viewVal = parseFloat(span.textContent) || 0;
    const minB = Math.min(viewVal, limMin);
    const maxB = Math.max(viewVal, limMax);
    let moved = false;

    if (field.hint === 'rotomask' && typeof rotoMaskSync === 'function') {
      rotoMaskSync();
    }

    const onMove = (ev) => {
      const dx = ev.clientX - startX;
      if (!moved && Math.abs(dx) > 3) moved = true;
      if (moved) {
        const mult = ev.shiftKey ? 10 : 1;
        let v = viewVal + dx * step * mult;
        if (field.ctl === 'int') v = Math.round(v);
        else {
          const dec = (step.toString().split('.')[1] || '').length;
          v = parseFloat(v.toFixed(Math.max(dec, 1)));
        }
        v = Math.max(minB, Math.min(maxB, v));
        span.textContent = v;
        span.setAttribute('aria-valuenow', v);
        input.value = v;
        const slider = document.getElementById('st_' + field.key + '_slider');
        if (slider) slider.value = Math.max(field.min != null ? field.min : -Infinity, Math.min(field.max != null ? field.max : Infinity, v));
        if (field.ctl === 'angle') updateAngleDial(field.key, v);
        stEdit();
        if (field.hint === 'rotomask' && typeof rotoMaskSync === 'function') rotoMaskSync();
      }
    };

    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      if (field.hint === 'rotomask' && typeof rotoMaskHide === 'function') rotoMaskHide();
      if (!moved) {
        openEdit();
      }
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      if (e.preventDefault) e.preventDefault();
      commitInput();
      span.focus();
    } else if (e.key === 'Escape') {
      if (e.preventDefault) e.preventDefault();
      cancelEdit();
    }
  });

  input.addEventListener('blur', () => {
    commitInput();
  });
}

function createAngleDial(key, field) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'stangle-dial');
  svg.setAttribute('id', 'st_dial_' + key);
  svg.setAttribute('width', '32');
  svg.setAttribute('height', '32');
  svg.setAttribute('viewBox', '0 0 32 32');

  const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  circle.setAttribute('cx', '16');
  circle.setAttribute('cy', '16');
  circle.setAttribute('r', '14');
  circle.setAttribute('fill', 'none');
  circle.setAttribute('stroke', 'var(--iron)');
  circle.setAttribute('stroke-width', '1.5');
  svg.appendChild(circle);

  const needle = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  needle.setAttribute('class', 'stangle-needle');
  needle.setAttribute('x1', '16');
  needle.setAttribute('y1', '16');
  needle.setAttribute('x2', '28');
  needle.setAttribute('y2', '16');
  needle.setAttribute('stroke', 'var(--tx)');
  needle.setAttribute('stroke-width', '1.5');
  svg.appendChild(needle);

  const centerDot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  centerDot.setAttribute('cx', '16');
  centerDot.setAttribute('cy', '16');
  centerDot.setAttribute('r', '2');
  centerDot.setAttribute('fill', 'var(--tx)');
  svg.appendChild(centerDot);

  svg.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const update = (ev) => {
      const r = svg.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      let d = Math.round(Math.atan2(ev.clientY - cy, ev.clientX - cx) * 180 / Math.PI);
      if (d < 0) d += 360;
      const span = document.getElementById('st_' + key + '_val');
      const input = document.getElementById('st_' + key + '_input');
      const slider = document.getElementById('st_' + key + '_slider');
      if (span) span.textContent = d;
      if (input) input.value = d;
      if (slider) slider.value = d;
      updateAngleDial(key, d);
      stEdit();
    };
    const onUp = () => {
      window.removeEventListener('mousemove', update);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', update);
    window.addEventListener('mouseup', onUp);
    update(e);
  });

  return svg;
}

function updateAngleDial(key, deg) {
  const dial = document.getElementById('st_dial_' + key);
  if (!dial) return;
  const needle = dial.querySelector('.stangle-needle');
  if (!needle) return;
  const rad = (parseFloat(deg) || 0) * Math.PI / 180;
  const x2 = 16 + 12 * Math.cos(rad);
  const y2 = 16 + 12 * Math.sin(rad);
  needle.setAttribute('x2', x2.toFixed(1));
  needle.setAttribute('y2', y2.toFixed(1));
}

function stSliderInput(key, val) {
  const field = findFieldByKey(key);
  if (!field) return;
  const span = document.getElementById('st_' + key + '_val');
  const input = document.getElementById('st_' + key + '_input');
  let v = parseFloat(val);
  if (field.ctl === 'int') v = Math.round(v);
  if (span) {
    span.textContent = v;
    span.setAttribute('aria-valuenow', v);
  }
  if (input) input.value = v;
  if (field.ctl === 'angle') updateAngleDial(key, v);
  stEdit();
  if (field.hint === 'rotomask' && typeof rotoMaskSync === 'function') rotoMaskSync();
}

function stToggleLayer(layerId, toggleKey, checked) {
  if (!CURSTYLE) CURSTYLE = JSON.parse(JSON.stringify((STSCHEMA && STSCHEMA.base) || {}));
  if (toggleKey === 'disclaimer') {
    if (!checked) CURSTYLE.disclaimer = '';
    else if (CURSTYLE.disclaimer === '') CURSTYLE.disclaimer = null;
  } else {
    CURSTYLE[toggleKey] = checked;
  }
  stEdit();
}

function stToggleGroup(groupId, toggleKey, checked) {
  if (!CURSTYLE) CURSTYLE = JSON.parse(JSON.stringify((STSCHEMA && STSCHEMA.base) || {}));
  if (toggleKey === 'disclaimer') {
    if (!checked) CURSTYLE.disclaimer = '';
    else if (CURSTYLE.disclaimer === '') CURSTYLE.disclaimer = null;
  } else {
    CURSTYLE[toggleKey] = checked;
  }
  stEdit();
}

function stHexChange(key, v) {
  v = (v || '').trim();
  if (v && v[0] !== '#') v = '#' + v;
  if (/^#[0-9a-fA-F]{6}$/.test(v)) {
    v = v.toUpperCase();
    const hex = document.getElementById('st_' + key + '_hex');
    const col = document.getElementById('st_' + key + '_color');
    if (hex) hex.value = v;
    if (col) col.value = v;
    stEdit();
  } else {
    const field = findFieldByKey(key);
    if (field && field.ctl === 'color_opt' && !v) {
      const hex = document.getElementById('st_' + key + '_hex');
      if (hex) hex.value = '';
      stEdit();
    }
  }
}

function stHexInput(key, v) {
  v = (v || '').trim();
  if (v && v[0] !== '#') v = '#' + v;
  if (/^#[0-9a-fA-F]{6}$/.test(v)) {
    v = v.toUpperCase();
    const col = document.getElementById('st_' + key + '_color');
    if (col) col.value = v;
  }
}

function stColorSwatchChange(key, v) {
  const hex = document.getElementById('st_' + key + '_hex');
  if (hex) hex.value = v.toUpperCase();
  stEdit();
}

function fillStyleFields() {
  const s = CURSTYLE || {};
  if (!STSCHEMA || !STSCHEMA.layers) return;

  function walk(items) {
    for (const item of items) {
      if (item.type === 'field') {
        const val = s[item.key];
        const view = stView(item, val);

        if (item.ctl === 'bool') {
          const chk = document.getElementById('st_' + item.key);
          if (chk) chk.checked = !!view;
        } else if (item.ctl === 'color') {
          const col = document.getElementById('st_' + item.key + '_color');
          const hex = document.getElementById('st_' + item.key + '_hex');
          if (col) col.value = view;
          if (hex) hex.value = view.toUpperCase();
        } else if (item.ctl === 'color_opt') {
          const col = document.getElementById('st_' + item.key + '_color');
          const hex = document.getElementById('st_' + item.key + '_hex');
          if (col) col.value = view ? view : '#FFFFFF';
          if (hex) hex.value = view ? view.toUpperCase() : '';
        } else if (item.ctl === 'num' || item.ctl === 'int' || item.ctl === 'angle') {
          const span = document.getElementById('st_' + item.key + '_val');
          const inp = document.getElementById('st_' + item.key + '_input');
          const slider = document.getElementById('st_' + item.key + '_slider');
          if (span) {
            span.textContent = view;
            span.setAttribute('aria-valuenow', view);
          }
          if (inp) inp.value = view;
          if (slider) slider.value = Math.max(item.min != null ? item.min : -Infinity, Math.min(item.max != null ? item.max : Infinity, view));
          if (item.ctl === 'angle') updateAngleDial(item.key, view);
        } else if (item.ctl === 'point') {
          const span = document.getElementById('st_' + item.key + '_val');
          if (span) {
            const baseDefX = (STSCHEMA && STSCHEMA.base && STSCHEMA.base[item.key]) != null ? STSCHEMA.base[item.key] : 0.5;
            const baseDefY = (item.key2 && STSCHEMA && STSCHEMA.base && STSCHEMA.base[item.key2]) != null ? STSCHEMA.base[item.key2] : 0.5;
            const x = (s[item.key] != null ? s[item.key] : baseDefX).toFixed(2);
            const y = (s[item.key2] != null ? s[item.key2] : baseDefY).toFixed(2);
            span.textContent = x + ', ' + y;
          }
        } else if (item.ctl === 'textarea') {
          const ta = document.getElementById(item.key === 'disclaimer' ? 'st_disc_text' : ('st_' + item.key));
          if (ta) ta.value = view != null ? view : '';
        } else {
          const el = document.getElementById('st_' + item.key);
          if (el) el.value = view != null ? view : '';
        }
      } else if (item.type === 'group' || item.id) {
        if (item.toggle) {
          const chk = document.getElementById('st_' + item.toggle);
          if (chk) {
            if (item.toggle === 'disclaimer') {
              chk.checked = (s.disclaimer !== '');
            } else {
              const baseDef = (STSCHEMA && STSCHEMA.base) ? STSCHEMA.base[item.toggle] : false;
              const val = (s[item.toggle] !== undefined && s[item.toggle] !== null) ? s[item.toggle] : baseDef;
              chk.checked = Boolean(val);
            }
          }
        }
        if (item.items) walk(item.items);
      }
    }
  }

  walk(STSCHEMA.layers);
  updateStyleVisibility();

  if (typeof updateHlFontList === 'function') updateHlFontList();
  if (typeof reflectStyle === 'function') reflectStyle();
  if (typeof syncSldnums === 'function') syncSldnums();
  if (typeof syncSubTabUI === 'function') syncSubTabUI();
  if (typeof applyStyleHlColor === 'function') applyStyleHlColor();
  if (typeof styleSubPos === 'function') styleSubPos();
  if (typeof aewUpdateCaptionUI === 'function') aewUpdateCaptionUI();
  if (typeof updateStyleDiffDots === 'function') updateStyleDiffDots();
  if (typeof renderLayerOrderUI === 'function') renderLayerOrderUI();
}

function stEdit() {
  CURSTYLE = CURSTYLE || {};
  if (typeof STYLE_TOUCHED !== 'undefined') STYLE_TOUCHED = true;
  if (!STSCHEMA || !STSCHEMA.layers) return;

  // hl_font пишется как null при снятой галке «Выделять жирным» (как в старом stEdit).
  // Галку читаем по её полю из схемы: id старой разметки (st_hlbold) в панели не существует,
  // из-за чего галка не читалась вовсе и hl_font не обнулялся.
  const hlBoldVal = stReadView('hl_bold');

  function walk(items) {
    for (const item of items) {
      if (item.type === 'field') {
        let view = null;
        if (item.ctl === 'bool') {
          const chk = document.getElementById('st_' + item.key);
          view = chk ? chk.checked : false;
        } else if (item.ctl === 'color') {
          const hex = document.getElementById('st_' + item.key + '_hex');
          const col = document.getElementById('st_' + item.key + '_color');
          view = hex && hex.value ? hex.value : (col ? col.value : '');
        } else if (item.ctl === 'color_opt') {
          const hex = document.getElementById('st_' + item.key + '_hex');
          view = hex ? hex.value.trim() : '';
        } else if (item.ctl === 'num' || item.ctl === 'int' || item.ctl === 'angle') {
          const span = document.getElementById('st_' + item.key + '_val');
          const inp = document.getElementById('st_' + item.key + '_input');
          view = span && span.style.display !== 'none' ? span.textContent : (inp ? inp.value : '');
        } else if (item.ctl === 'point') {
          if (CURSTYLE[item.key] === undefined) {
            const baseDefX = (STSCHEMA && STSCHEMA.base && STSCHEMA.base[item.key]);
            CURSTYLE[item.key] = baseDefX != null ? baseDefX : 0.5;
          }
          if (item.key2 && CURSTYLE[item.key2] === undefined) {
            const baseDefY = (STSCHEMA && STSCHEMA.base && STSCHEMA.base[item.key2]);
            CURSTYLE[item.key2] = baseDefY != null ? baseDefY : 0.5;
          }
          continue;
        } else if (item.ctl === 'textarea') {
          if (item.key === 'disclaimer') {
            const chk = document.getElementById('st_disclaimer');
            const show = chk ? chk.checked : (CURSTYLE.disclaimer !== '');
            const ta = document.getElementById('st_disc_text');
            const txt = ta ? ta.value.trim() : '';
            CURSTYLE.disclaimer = show ? (txt || null) : '';
            continue;
          }
          const ta = document.getElementById('st_' + item.key);
          view = ta ? ta.value : '';
        } else if (item.ctl === 'layer_order') {
          CURSTYLE.layer_order = stStore(item, CURSTYLE.layer_order);
          continue;
        } else {
          const el = document.getElementById('st_' + item.key);
          view = el ? el.value : '';
        }

        const opt = { hl_bold: hlBoldVal, orig: CURSTYLE[item.key] };
        CURSTYLE[item.key] = stStore(item, view, opt);
      } else if (item.type === 'group' || item.id) {
        if (item.toggle) {
          const chk = document.getElementById('st_' + item.toggle);
          if (chk) {
            if (item.toggle === 'disclaimer') {
              if (!chk.checked) CURSTYLE.disclaimer = '';
              else if (CURSTYLE.disclaimer === '') CURSTYLE.disclaimer = null;
            } else {
              CURSTYLE[item.toggle] = chk.checked;
            }
          } else {
            if (item.toggle === 'disclaimer') {
              if (CURSTYLE.disclaimer === undefined) CURSTYLE.disclaimer = null;
            } else if (CURSTYLE[item.toggle] === undefined || CURSTYLE[item.toggle] === null) {
              const baseDef = (STSCHEMA && STSCHEMA.base) ? STSCHEMA.base[item.toggle] : false;
              CURSTYLE[item.toggle] = Boolean(baseDef);
            }
          }
        }
        if (item.items) walk(item.items);
      }
    }
  }

  walk(STSCHEMA.layers);

  const imEl = document.getElementById('intromode');
  if (imEl && imEl.value) CURSTYLE.intro_mode = imEl.value;
  // V10: в ДАННЫЕ пишем исходное «кастом», а не перевод: в английском интерфейсе
  // t('кастом') клал в стиль 'custom', и метка уезжала в состояние/сборку другой строкой.
  // Переводится только показ — селектор стилей и подсказки зовут t(label) сами.
  CURSTYLE.label = CURSTYLE.label || 'кастом';

  updateStyleVisibility();

  if (typeof reflectStyle === 'function') reflectStyle();
  if (typeof updateHlFontList === 'function') updateHlFontList();
  if (typeof syncSldnums === 'function') syncSldnums();
  if (typeof syncSubTabUI === 'function') syncSubTabUI();
  if (typeof applyStyleHlColor === 'function') applyStyleHlColor();
  if (typeof styleSubPos === 'function') styleSubPos();
  if (typeof syncDbSliders === 'function') syncDbSliders();
  if (typeof applyDbGains === 'function') applyDbGains();
  if (typeof aewUpdateCaptionUI === 'function') aewUpdateCaptionUI();
  if (typeof updateStyleDiffDots === 'function') updateStyleDiffDots();
  if (typeof captureAE === 'function') captureAE();
  if (typeof ipvPlanSoon === 'function') ipvPlanSoon();
  if (typeof updateStyleSaveUI === 'function') updateStyleSaveUI();
}

function updateStyleVisibility() {
  if (!STSCHEMA || !STSCHEMA.layers) return;
  const s = CURSTYLE || {};

  function evalShowIf(showIf) {
    if (!showIf || !showIf.key) return true;
    const val = s[showIf.key];
    if (showIf.eq !== undefined) return val === showIf.eq;
    if (showIf.ne !== undefined) return val !== showIf.ne;
    if (showIf.in !== undefined) return Array.isArray(showIf.in) && showIf.in.includes(val);
    return true;
  }

  function walk(items) {
    for (const item of items) {
      if (item.type === 'field') {
        const vis = evalShowIf(item.show_if);
        const row = document.getElementById('strow_' + item.key);
        const sldRow = document.getElementById('stslider_row_' + item.key);
        if (row) row.style.display = vis ? '' : 'none';
        if (sldRow) {
          if (!vis) sldRow.style.display = 'none';
          // V2: при возврате видимости — восстановить состояние слайдера по раскрытости
          // треугольника, иначе он останется скрытым навсегда
          else sldRow.style.display = isExpanded('field_' + item.key) ? '' : 'none';
        }
      } else if (item.type === 'group' || item.id) {
        if (item.toggle) {
          const on = isNodeOn(item);
          const safeId = item.type === 'group' ? item.id.replace(/\./g, '_') : item.id;
          const bodyEl = document.getElementById('stbody_' + safeId);
          if (bodyEl) {
            const exp = isExpanded(item.id);
            bodyEl.style.display = (on && exp) ? '' : 'none';
          }
          const chk = document.getElementById('st_' + item.toggle);
          if (chk) chk.checked = on;
          const rowEl = (item.type === 'group') ? document.querySelector(`.stgroup[data-tw="${item.id}"]`) : document.querySelector(`.stlayer[data-tw="${item.id}"]`);
          if (rowEl) {
            rowEl.classList.toggle('st-off', !on);
          }
        }
        if (item.items) walk(item.items);
      }
    }
  }

  walk(STSCHEMA.layers);
}

function updateStyleDiffDots() {
  if (!STSCHEMA || !STSCHEMA.layers) return;
  const s = CURSTYLE || {};
  const orig = getStyleParent();
  const baseDef = STSCHEMA.base || {};

  function getNormVal(obj, key, nullable) {
    let v = obj ? obj[key] : undefined;
    if (v === undefined || (!nullable && v === null)) {
      v = baseDef[key];
    }
    return v;
  }

  function isFieldChanged(field) {
    const curVal = getNormVal(s, field.key, field.nullable);
    const defVal = getNormVal(orig, field.key, field.nullable);

    if (field.ctl === 'color') {
      const c1 = stRgb2hex(curVal || [1, 1, 1]).toLowerCase();
      const c2 = stRgb2hex(defVal || [1, 1, 1]).toLowerCase();
      return c1 !== c2;
    }
    if (field.ctl === 'color_opt') {
      const empty1 = !curVal || !Array.isArray(curVal);
      const empty2 = !defVal || !Array.isArray(defVal);
      if (empty1 && empty2) return false;
      if (empty1 !== empty2) return true;
      return stRgb2hex(curVal).toLowerCase() !== stRgb2hex(defVal).toLowerCase();
    }
    if (field.ctl === 'point') {
      const x1 = curVal != null ? curVal : 0.5;
      const x2 = defVal != null ? defVal : 0.5;
      const curY = getNormVal(s, field.key2, false);
      const defY = getNormVal(orig, field.key2, false);
      const y1 = curY != null ? curY : 0.5;
      const y2 = defY != null ? defY : 0.5;
      return Math.abs(Number(x1) - Number(x2)) > 1e-4 || Math.abs(Number(y1) - Number(y2)) > 1e-4;
    }
    if (field.ctl === 'layer_order') {
      const arr1 = Array.isArray(curVal) ? curVal : [];
      const arr2 = Array.isArray(defVal) ? defVal : [];
      return JSON.stringify(arr1) !== JSON.stringify(arr2);
    }
    if (field.ctl === 'num' || field.ctl === 'int' || field.ctl === 'angle') {
      if (field.nullable) {
        const e1 = (curVal === '' || curVal == null);
        const e2 = (defVal === '' || defVal == null);
        if (e1 && e2) return false;
        if (e1 !== e2) return true;
      }
      const n1 = curVal != null ? curVal : 0;
      const n2 = defVal != null ? defVal : 0;
      return Math.abs(Number(n1) - Number(n2)) > 1e-4;
    }
    if (field.ctl === 'bool') {
      return Boolean(curVal) !== Boolean(defVal);
    }
    if (field.ctl === 'font' || field.ctl === 'textarea' || field.ctl === 'file' || field.ctl === 'select') {
      if (field.key === 'disclaimer') {
        const v1 = (curVal === undefined || curVal === null) ? null : curVal;
        const v2 = (defVal === undefined || defVal === null) ? null : defVal;
        return v1 !== v2;
      }
      const e1 = (curVal === '' || curVal == null);
      const e2 = (defVal === '' || defVal == null);
      if (e1 && e2) return false;
      if (e1 !== e2) return true;
      return curVal !== defVal;
    }
    return curVal !== defVal;
  }

  function checkNode(item) {
    let changed = false;
    if (item.toggle) {
      if (item.toggle === 'disclaimer') {
        const curD = getNormVal(s, 'disclaimer', true);
        const defD = getNormVal(orig, 'disclaimer', true);
        const on1 = (curD !== '' && curD !== false);
        const on2 = (defD !== '' && defD !== false);
        if (on1 !== on2) changed = true;
      } else {
        const curT = getNormVal(s, item.toggle, false);
        const defT = getNormVal(orig, item.toggle, false);
        if (Boolean(curT) !== Boolean(defT)) changed = true;
      }
    }
    if (item.type === 'field') {
      const fCh = isFieldChanged(item);
      const dot = document.querySelector(`[data-dot-key="${item.key}"]`);
      if (dot) dot.classList.toggle('st-changed', fCh);
      if (fCh) changed = true;
    }
    if (item.items) {
      for (const ch of item.items) {
        if (checkNode(ch)) changed = true;
      }
    }
    if (item.type === 'group') {
      const dot = document.querySelector(`[data-dot-group="${item.id}"]`);
      if (dot) dot.classList.toggle('st-changed', changed);
    } else if (item.id && !item.type) {
      const dot = document.querySelector(`[data-dot-layer="${item.id}"]`);
      if (dot) dot.classList.toggle('st-changed', changed);
    }
    return changed;
  }

  for (const layer of STSCHEMA.layers) {
    checkNode(layer);
  }

  if (typeof updateStyleSaveUI === 'function') updateStyleSaveUI();
}

function stResetKey(key) {
  if (!CURSTYLE) return;
  const orig = getStyleParent();
  const field = findFieldByKey(key);
  if (!field) return;
  const baseDef = (STSCHEMA && STSCHEMA.base) || {};
  const defVal = (orig[key] !== undefined && (field.nullable || orig[key] !== null)) ? orig[key] : baseDef[key];
  CURSTYLE[key] = Array.isArray(defVal) ? [...defVal] : defVal;
  if (field.ctl === 'point' && field.key2) {
    const defVal2 = (orig[field.key2] !== undefined && orig[field.key2] !== null) ? orig[field.key2] : baseDef[field.key2];
    CURSTYLE[field.key2] = defVal2;
  }
  fillStyleFields();
  stEdit();
}

function stReset(groupId) {
  if (!CURSTYLE || !STSCHEMA || !STSCHEMA.layers) return;
  const orig = getStyleParent();
  const baseDef = (STSCHEMA && STSCHEMA.base) || {};

  function findGroup(items) {
    for (const it of items) {
      if (it.id === groupId) return it;
      if (it.items) {
        const f = findGroup(it.items);
        if (f) return f;
      }
    }
    return null;
  }

  const group = findGroup(STSCHEMA.layers);
  if (!group) return;

  function collectKeys(it, out) {
    if (it.toggle) out.push(it.toggle);
    if (it.key) out.push(it.key);
    if (it.key2) out.push(it.key2);
    if (it.items) {
      for (const ch of it.items) collectKeys(ch, out);
    }
  }

  const keys = [];
  collectKeys(group, keys);
  for (const k of keys) {
    const f = findFieldByKey(k);
    const nullable = f ? f.nullable : false;
    const defVal = (orig[k] !== undefined && (nullable || orig[k] !== null)) ? orig[k] : baseDef[k];
    CURSTYLE[k] = Array.isArray(defVal) ? [...defVal] : defVal;
  }
  fillStyleFields();
  stEdit();
}

function stRefresh(key) {
  if (!STSCHEMA || !STSCHEMA.layers) return;
  const field = findFieldByKey(key);
  if (!field) return;
  const s = CURSTYLE || {};
  const val = s[key];
  const view = stView(field, val);

  if (field.ctl === 'bool') {
    const el = document.getElementById('st_' + key);
    if (el) el.checked = !!view;
  } else if (field.ctl === 'color') {
    const col = document.getElementById('st_' + key + '_color');
    const hex = document.getElementById('st_' + key + '_hex');
    if (col) col.value = view;
    if (hex) hex.value = view.toUpperCase();
  } else if (field.ctl === 'color_opt') {
    const col = document.getElementById('st_' + key + '_color');
    const hex = document.getElementById('st_' + key + '_hex');
    if (col) col.value = view ? view : '#FFFFFF';
    if (hex) hex.value = view ? view.toUpperCase() : '';
  } else if (field.ctl === 'num' || field.ctl === 'int' || field.ctl === 'angle') {
    const span = document.getElementById('st_' + key + '_val');
    const inp = document.getElementById('st_' + key + '_input');
    const slider = document.getElementById('st_' + key + '_slider');
    if (span) {
      span.textContent = view;
      span.setAttribute('aria-valuenow', view);
    }
    if (inp) inp.value = view;
    if (slider) slider.value = Math.max(field.min != null ? field.min : -Infinity, Math.min(field.max != null ? field.max : Infinity, view));
    if (field.ctl === 'angle') updateAngleDial(key, view);
  } else if (field.ctl === 'point') {
    const span = document.getElementById('st_' + key + '_val');
    if (span) {
      const x = s[field.key] != null ? s[field.key].toFixed(2) : '0.50';
      const y = s[field.key2] != null ? s[field.key2].toFixed(2) : '0.50';
      span.textContent = x + ', ' + y;
    }
  } else if (field.ctl === 'textarea') {
    const ta = document.getElementById(key === 'disclaimer' ? 'st_disc_text' : ('st_' + key));
    if (ta) ta.value = view != null ? view : '';
  } else {
    const el = document.getElementById('st_' + key);
    if (el) el.value = view != null ? view : '';
  }

  updateStyleVisibility();
  updateStyleDiffDots();
}

// Экспорт в глобальную область видимости браузера
if (typeof window !== 'undefined') {
  // V9: STSCHEMA/SFX_PREFIX присваивались здесь СНИМКОМ на момент загрузки скрипта,
  // то есть null и {}. Схема приезжает позже (loadStyleSchema), поэтому наружу отдаём
  // геттеры — внешний код видит текущее значение, а не пустышку.
  Object.defineProperty(window, 'STSCHEMA', { get: () => STSCHEMA, configurable: true });
  window.loadStyleSchema = loadStyleSchema;
  window.stConv = stConv;
  window.stView = stView;
  window.stStore = stStore;
  window.renderStylePanel = renderStylePanel;
  window.fillStyleFields = fillStyleFields;
  window.stEdit = stEdit;
  window.updateStyleDiffDots = updateStyleDiffDots;
  window.updateStyleVisibility = updateStyleVisibility;
  window.stReset = stReset;
  window.stResetKey = stResetKey;
  window.stRefresh = stRefresh;
  window.stReadView = stReadView;
  Object.defineProperty(window, 'SFX_PREFIX', { get: () => SFX_PREFIX, configurable: true });
  Object.defineProperty(window, 'SFX_ISVIDEO', { get: () => SFX_ISVIDEO, configurable: true });
}
