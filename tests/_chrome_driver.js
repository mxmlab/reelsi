// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// Драйвер настоящего Chrome для поведенческих тестов интерфейса (вызывается из _chrome_stand.py).
//
// Запуск: node _chrome_driver.js <spec.json>. Spec: {chrome, port, userdata, url, w, h, steps}.
// Шаги выполняются по порядку через DevTools Protocol (Chrome сам обрабатывает ввод мыши —
// это настоящие события браузера, а не вызовы обработчиков):
//   {op:"eval", js, name}          — выполнить JS на странице, значение пойдёт в results[name]
//   {op:"wait", ms}                — пауза в реальном времени
//   {op:"mouse", type, sel, fx, fy, x, y, button, clickCount}
//                                  — одно событие мыши (type: mouseMoved|mousePressed|mouseReleased)
//   {op:"drag", sel, fx, fy, dx, dy, button, steps}
//                                  — нажать в точке элемента, провести на (dx,dy), отпустить
// Ответ: одна строка JSON {results, error?}. Процесс Chrome, который запустили, убивается по PID.
'use strict';
const fs = require('fs');
const { spawn } = require('child_process');

const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const BUTTON_MASK = { none: 0, left: 1, right: 2, middle: 4 };

async function main() {
  const child = spawn(spec.chrome, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=' + spec.port, '--user-data-dir=' + spec.userdata,
    '--window-size=' + spec.w + ',' + spec.h, '--autoplay-policy=no-user-gesture-required',
    '--allow-file-access-from-files', '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
    'about:blank',
  ], { stdio: 'ignore' });
  const out = { results: {} };
  try {
    let page = null;
    for (let i = 0; i < 200 && !page; i++) {
      try {
        const list = await (await fetch('http://127.0.0.1:' + spec.port + '/json/list')).json();
        page = list.find((t) => t.type === 'page') || null;
      } catch (e) { /* Chrome ещё поднимается */ }
      if (!page) await sleep(100);
    }
    if (!page) throw new Error('Chrome не открыл DevTools-порт');
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('нет DevTools-сокета')); });
    let seq = 0;
    const pending = new Map();
    const waiters = [];
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) {
        const p = pending.get(m.id);
        pending.delete(m.id);
        if (m.error) p.rej(new Error(m.error.message)); else p.res(m.result);
      } else if (m.method) {
        const i = waiters.findIndex((w) => w.method === m.method);
        if (i >= 0) { const w = waiters.splice(i, 1)[0]; w.res(m); }
      }
    };
    const send = (method, params = {}) => new Promise((res, rej) => {
      const id = ++seq; pending.set(id, { res, rej }); ws.send(JSON.stringify({ id, method, params }));
    });
    const waitEvent = (method, ms = 30000) => new Promise((res, rej) => {
      const t = setTimeout(() => rej(new Error('таймаут ' + method)), ms);
      waiters.push({ method, res: (m) => { clearTimeout(t); res(m); } });
    });
    await send('Page.enable');
    await send('Runtime.enable');
    const loaded = waitEvent('Page.loadEventFired');
    await send('Page.navigate', { url: spec.url });
    await loaded;
    await sleep(spec.settle_ms || 300);

    const evalJs = async (expr) => {
      const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true, timeout: 30000 });
      if (r.exceptionDetails) {
        const d = r.exceptionDetails;
        throw new Error('JS: ' + ((d.exception && d.exception.description) || d.text || 'ошибка'));
      }
      return r.result.value;
    };
    const point = async (sel, fx, fy) => {
      const v = await evalJs('(() => { const e = document.querySelector(' + JSON.stringify(sel) + '); if (!e) return null;'
        + ' const r = e.getBoundingClientRect(); return [r.left + r.width * ' + fx + ', r.top + r.height * ' + fy + ']; })()');
      if (!v) throw new Error('нет элемента ' + sel);
      return v;
    };
    const mouse = (type, x, y, button, clickCount, mods) => send('Input.dispatchMouseEvent', {
      type, x, y, button, clickCount, modifiers: mods || 0,
      buttons: type === 'mouseReleased' ? 0 : BUTTON_MASK[button || 'none'],
    });

    for (const st of spec.steps) {
      if (st.op === 'eval') {
        const v = await evalJs(st.js);
        if (st.name) out.results[st.name] = v;
      } else if (st.op === 'wait') {
        await sleep(st.ms);
      } else if (st.op === 'mouse') {
        const [x, y] = st.sel ? await point(st.sel, st.fx, st.fy) : [st.x, st.y];
        await mouse(st.type, x, y, st.button || 'left', st.clickCount || 1);
      } else if (st.op === 'key') {
        // настоящая клавиша (Input.dispatchKeyEvent): key, code, vk — как у браузерного ввода
        const down = { type: 'keyDown', key: st.key, code: st.code || st.key, windowsVirtualKeyCode: st.vk || 0 };
        if (st.key === 'Enter') down.text = '\r';
        await send('Input.dispatchKeyEvent', down);
        await send('Input.dispatchKeyEvent', Object.assign({}, down, { type: 'keyUp', text: undefined }));
      } else if (st.op === 'text') {
        await send('Input.insertText', { text: st.text });
      } else if (st.op === 'mouseAt') {
        // координаты считает JS на странице: [x, y] в css-пикселях окна
        const v = await evalJs(st.js);
        if (!v) throw new Error('mouseAt: JS вернул пусто');
        await mouse(st.type, v[0], v[1], st.button || 'none', st.clickCount || 0);
      } else if (st.op === 'drag') {
        const [x0, y0] = await point(st.sel, st.fx, st.fy);
        const x1 = x0 + st.dx, y1 = y0 + st.dy;
        const btn = st.button || 'left';
        const md = st.modifiers || 0;
        await mouse('mouseMoved', x0, y0, 'none', 0, md);
        await mouse('mousePressed', x0, y0, btn, 1, md);
        const n = st.steps || 8;
        for (let i = 1; i <= n; i++) {
          await mouse('mouseMoved', x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n, btn, 0, md);
        }
        await mouse('mouseReleased', x1, y1, btn, 1, md);
      } else {
        throw new Error('неизвестный шаг ' + st.op);
      }
      await sleep(st.after_ms || 0);
    }
  } catch (e) {
    out.error = String((e && e.message) || e);
  } finally {
    try { child.kill(); } catch (e) { /* уже завершён */ }
  }
  process.stdout.write(JSON.stringify(out) + '\n');
}

main().then(() => process.exit(0), () => process.exit(1));
