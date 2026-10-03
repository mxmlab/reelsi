// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
//
// Съёмщик кадров для рендера без After Effects: Chrome без окна + протокол DevTools.
//
// Зачем отдельная программа: кадр рисует наш же предпросмотр (страница /render), а
// снять его и отдать потоком байт умеет только браузер. Python (core/webrender.py)
// запускает этот скрипт, читает кадры из stdout и отдаёт их ffmpeg.
//
// Ни одной зависимости: WebSocket и fetch встроены в Node 22, npm ставить нельзя.
//
// Формат вывода (stdout): поток кадров, каждый — 4 байта длины (старший вперёд) и
// JPEG (формат задаёт `--format`, по умолчанию jpeg: снимок 1080x1920 в PNG весит
// мегабайты, и почти всё это время уходит на кодирование в браузере и base64 через
// протокол DevTools). Прогресс и служебные метки (stderr), по строке на кадр:
//     #chrome-pid <pid>   — PID СВОЕГО Chrome: по нему Python гасит его при отмене.
//                           По имени убивать нельзя: на машине открыт браузер человека.
//     #gpu <чем рисует>   — аппаратная отрисовка, программный запасной путь или
//                           «браузер не ответил»: кадр — самая дорогая часть съёмки,
//                           и по этой строке видно, досталась рендеру карта или нет.
//     #ready <w>x<h>@<fps>
//     кадр <i>/<n>
//     #done <n>
//     #timing seek=… paint=… shot=… write=… (мс, среднее)  — каждые TIMING_EVERY кадров
//
// Разбивка времени — то, ради чего она здесь: по ней видно, что именно тормозит рендер
// (ждать кадр <video>, рисовать, снимать или отдавать байты), и её же разбирает Python
// (core.webrender.TIMING_RE). Формат печати живёт в webrender/timing.mjs — одном на
// обе стороны и на тест, чтобы он не разошёлся с разбором молча.
//
// Запуск (обычно его делает core/webrender.py):
//   node core/webrender/capture.mjs --url http://127.0.0.1:5001/render?... \
//        --w 1080 --h 1920 --fps 60 --start 300 --frames 180 --profile <папка> \
//        --ready-timeout-ms 180000 --format jpeg --jpeg-quality 95
//
// ЕДИНИЦЫ В ИМЕНИ КЛЮЧА. Единица расходилась молча: Python слал секунды (180), а
// здесь они читались как миллисекунды — и страница объявлялась неготовой через
// 180 мс. Поэтому у времени в ключе стоит единица, и рядом — своя проверка:
//   --start   кадр (целое),     --frames  кадров (целое),
//   --fps     кадров в секунду, --ready-timeout-ms  миллисекунды (единственное время здесь).
// Разбор ключей сверяет Python и съёмщик тест: tests/test_webrender.py, `--parse-only`.
//
// `--parse-only` печатает разобранные значения одним JSON в stdout и выходит, не
// трогая ни Chrome, ни сеть, — им проверяется, что единицы и типы сторон сошлись.

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import net from 'node:net';
import { TIMING_EVERY, timingLine } from './timing.mjs';

// --------------------------------------------------------------------------- //
// Аргументы
// --------------------------------------------------------------------------- //
function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) { out[key] = true; continue; }
    out[key] = next; i++;
  }
  return out;
}

// Разбор числа со значением по умолчанию. Ноль — законное число (кадр 0, начало с
// нулевой секунды), поэтому на «пусто» проверяется само значение ключа, а не его
// истинность: `||` подменил бы ноль значением по умолчанию.
const num = (v, def) => {
  const s = v === undefined || v === null || v === true ? String(def) : String(v);
  const n = Number(s);
  if (!Number.isFinite(n)) throw new Error(`«${s}» — не число`);
  return n;
};

const args = parseArgs(process.argv.slice(2));
const URL_PAGE = String(args.url || '');
const W = num(args.w, 0);
const H = num(args.h, 0);
const FPS = num(args.fps, 60);
const START = num(args.start, 0);         // кадр: целое, но дробное принимаем и округляем
const FRAMES = num(args.frames, 0);       // кадров: целое
const PROFILE = String(args.profile || '');
const READY_MS = num(args['ready-timeout-ms'], 180000);   // ЕДИНСТВЕННОЕ время в мс
// Формат снимка: только те, что принимает Page.captureScreenshot и умеет ffmpeg на
// входе (`mjpeg` для jpeg, `png` для png — см. core/webrender.py).
const FORMATS = { jpeg: 'jpeg', png: 'png' };
const FORMAT = String(args.format === undefined || args.format === true ? 'jpeg' : args.format);
const JPEG_Q = num(args['jpeg-quality'], 95);             // качество JPEG, 0–100

const err = (line) => process.stderr.write(line + '\n');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

if (!URL_PAGE || !(W > 0) || !(H > 0) || !(FPS > 0) || !(FRAMES > 0) || !PROFILE) {
  err('#ошибка: нужны --url --w --h --fps --frames --profile');
  process.exit(2);
}
if (!Number.isInteger(START) || START < 0) {
  err(`#ошибка: --start — номер кадра (целое >= 0), пришло ${JSON.stringify(args.start)}`);
  process.exit(2);
}
if (!Number.isInteger(FRAMES)) {          // кадров целое число: половина кадра — не кадр
  err(`#ошибка: --frames — целое число кадров, пришло ${JSON.stringify(args.frames)}`);
  process.exit(2);
}
if (!(READY_MS >= 0)) {
  err(`#ошибка: --ready-timeout-ms — миллисекунды (число >= 0), пришло `
    + `${JSON.stringify(args['ready-timeout-ms'])}`);
  process.exit(2);
}
if (!FORMATS[FORMAT]) {
  err(`#ошибка: --format — ${Object.keys(FORMATS).join(' или ')}, пришло ${JSON.stringify(args.format)}`);
  process.exit(2);
}
if (!(JPEG_Q >= 0) || !(JPEG_Q <= 100)) {
  err(`#ошибка: --jpeg-quality — 0…100, пришло ${JSON.stringify(args['jpeg-quality'])}`);
  process.exit(2);
}

// Только разбор: печатаем разобранное и выходим — ни Chrome, ни сети. Единицы и типы
// проверяются тестом на РЕАЛЬНОМ коде разбора, а не на копии условий в Python.
if (args['parse-only'] === true) {
  process.stdout.write(JSON.stringify({
    url: URL_PAGE,
    w: W,
    h: H,
    fps: FPS,
    start: START,
    frames: FRAMES,
    profile: PROFILE,
    readyTimeoutMs: READY_MS,
    format: FORMAT,
    jpegQuality: JPEG_Q,
    timingEvery: TIMING_EVERY,
    chrome: args.chrome === true || args.chrome === undefined ? null : String(args.chrome),
  }) + '\n');
  process.exit(0);
}

// --------------------------------------------------------------------------- //
// Chrome: путь, свободный порт DevTools, запуск
// --------------------------------------------------------------------------- //
function chromePath(explicit) {
  const cands = [
    explicit,
    process.env.CHROME_PATH,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ].filter(Boolean);
  for (const c of cands) if (existsSync(c)) return c;
  throw new Error('Chrome не найден — укажи путь ключом --chrome');
}

function freePort() {
  return new Promise((res, rej) => {
    const srv = net.createServer();
    srv.on('error', rej);
    srv.listen(0, '127.0.0.1', () => {
      const p = srv.address().port;
      srv.close(() => res(p));
    });
  });
}

let chrome = null;

function killChrome() {
  // Только гашение — БЕЗ process.exit. Выход отсюда шёл ровно посередине уборки:
  // успешный прогон завершался кодом, выставленным ДО того, как станет известен
  // результат, а хвост stdout при этом не был дописан (на медленном диске последние
  // кадры могли не доехать). Код выхода теперь выставляет завершение main (см. низ).
  if (chrome && chrome.pid && chrome.exitCode === null) {
    try { chrome.kill('SIGKILL'); } catch { /* уже мёртв — гасить нечего */ }
    if (process.platform === 'win32') {
      // На Windows SIGKILL родителю не всегда снимает самого браузера — добираем деревом
      // по PID (НЕ по имени: чужой Chrome на машине трогать нельзя).
      try {
        spawn('taskkill', ['/F', '/T', '/PID', String(chrome.pid)], { stdio: 'ignore' });
      } catch { /* процесса уже нет */ }
    }
  }
}

let stopping = false;
function stop(code) {
  if (stopping) return;
  stopping = true;
  killChrome();
  flushStdout().finally(() => process.exit(code));
}
process.on('SIGTERM', () => stop(143));
process.on('SIGINT', () => stop(130));

async function launchChrome(port, exe) {
  // Флаги: без окна, без сети «на фоне», фиксированный масштаб пикселя и цветовой
  // профиль (иначе кадры одного и того же места выходили бы разными).
  //
  // АППАРАТНАЯ ОТРИСОВКА. Здесь стоял `--disable-gpu`: кадр собирался программно, и это
  // самая дорогая часть съёмки (живой вопрос владельца: «нельзя рендер на GPU? быстрее?»).
  // Кодирует уже видеокарта, а рисовал кадр процессор. Теперь браузеру разрешено
  // рисовать на карте, а если её нет — Chrome сам уходит на программный путь
  // (SwiftShader): за это отвечает `--enable-unsafe-swiftshader`, без него в новых
  // сборках программного запасного пути просто НЕТ и страница не отрисуется вовсе.
  // Что именно досталось рендеру, съёмщик печатает одной строкой `#gpu` (см. reportGpu):
  // по логу видно, аппаратная отрисовка или запасная.
  //
  // `--use-angle=d3d11` — ANGLE поверх Direct3D 11 (Windows); `--ignore-gpu-blocklist` —
  // не прятать карту из-за формального несоответствия списку (в headless он особенно
  // строг). `--enable-gpu` оставлен нарочно: у части сборок это единственный способ
  // поднять GPU-процесс без окна. `REELSI_CAPTURE_NO_GPU=1` возвращает прежний
  // программный путь — дверь на случай, если на чьей-то машине аппаратный кадр врёт.
  //
  // --disable-features=… : браузер в фоне тянет свои сервисы (подсказки, перевод,
  // MediaRouter, мастер оптимизации) и КАЧАЕТ модель — в логе это было видно как
  // `Created TensorFlow Lite XNNPACK delegate` и запросы GCM (`registration_request …
  // DEPRECATED_ENDPOINT`). Рендеру это не нужно вовсе: каждый такой запрос — время
  // старта страницы и лишний шум в логе.
  //
  // Одного --disable-background-networking для GCM не хватило: в логе владельца
  // оставалось `registration_request.cc … PHONE_REGISTRATION_ERROR`. Регистрацию в GCM
  // заводит служба push-сообщений (PushMessaging), а её включают расширения с фоновыми
  // страницами и приложения по умолчанию, которые ставятся в свежий профиль. Поэтому
  // вдобавок к фону выключаются расширения (в т.ч. компонентные — у них фоновые
  // страницы есть даже в чистом профиле), приложения по умолчанию и сама служба
  // push-сообщений; имена всех этих ключей сверены с установленным Chrome (есть в
  // chrome.dll), описание у них ровно такое (chrome_switches.cc).
  const flags = [
    '--headless=new',
    ...(process.env.REELSI_CAPTURE_NO_GPU ? ['--disable-gpu'] : [
      '--enable-gpu',
      '--use-angle=d3d11',
      '--ignore-gpu-blocklist',
      '--enable-unsafe-swiftshader',
    ]),
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--disable-default-apps',
    '--disable-component-extensions-with-background-pages',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-sync',
    '--disable-domain-reliability',
    '--metrics-recording-only',
    '--disable-features=OptimizationGuideModelDownloading,OptimizationHints,Translate,MediaRouter,PushMessaging',
    '--hide-scrollbars',
    '--mute-audio',
    '--autoplay-policy=no-user-gesture-required',
    '--force-device-scale-factor=1',
    '--force-color-profile=srgb',
    '--font-render-hinting=none',
    '--disable-lcd-text',
    '--disable-partial-raster',
    '--disable-threaded-animation',
    '--disable-threaded-scrolling',
    '--disable-checker-imaging',
    '--disable-image-animation-resync',
    '--run-all-compositor-stages-before-draw',
    '--disable-new-content-rendering-timeout',
    `--window-size=${W},${H}`,
    `--user-data-dir=${PROFILE}`,
    `--remote-debugging-port=${port}`,
    'about:blank',
  ];
  // Программа запуска — путь; `REELSI_CAPTURE_CHROME_APP` добавляет к нему аргументы
  // ПЕРЕД путём (обычно «node» — так проверки запускают вместо браузера свою программу,
  // не поднимая Chrome). Пусто — запуск ровно как был: первым идёт сам путь.
  const app = String(process.env.REELSI_CAPTURE_CHROME_APP || '').split(' ').filter(Boolean);
  const argv = app.length ? [...app, exe, ...flags] : [exe, ...flags];
  // stderr — НАСЛЕДУЕТСЯ, а не в трубу: труба на дочерний процесс это лишняя точка
  // отказа (в ограниченном окружении такой spawn падает EPERM), а фильтровать шум
  // браузера нечем — его и так видно одной-двумя строками при старте.
  const proc = spawn(argv[0], argv.slice(1), { stdio: ['ignore', 'ignore', 'inherit'] });
  err(`#chrome-pid ${proc.pid}`);
  return proc;
}

// Чем рисует браузер: одной строкой в лог рендера. Спрашиваем САМ Chrome
// (`SystemInfo.getInfo`), а не гадаем по флагам: флаги лишь разрешают аппаратную
// отрисовку, а досталась она или нет — решает карта, драйвер и список блокировки.
// Ответ ждём недолго: строка в логе важнее полноты, а молчащий браузер не должен
// держать старт съёмки.
const GPU_PROBE_MS = 5000;

async function reportGpu(cdp) {
  let info = null;
  let timer = null;
  try {
    info = await Promise.race([
      cdp.send('SystemInfo.getInfo', {}),
      new Promise((res) => { timer = setTimeout(() => res(null), GPU_PROBE_MS); }),
    ]);
  } catch {
    info = null;                       // старый Chrome без SystemInfo — просто «неизвестно»
  } finally {
    if (timer) clearTimeout(timer);    // таймер не держит процесс после ответа
  }
  const aux = (info && info.gpu && info.gpu.auxAttributes) || {};
  const soft = aux.softwareRendering;
  const dev = ((info && info.gpu && info.gpu.devices) || [])
    .map((d) => String(d.deviceString || d.vendorString || '')).filter(Boolean)[0] || '';
  if (soft === true) {
    err(`#gpu программная отрисовка (карта недоступна)${dev ? ' — ' + dev : ''}`);
  } else if (soft === false) {
    err(`#gpu аппаратная отрисовка${dev ? ' — ' + dev : ''}`);
  } else {
    err('#gpu браузер не ответил, чем рисует — продолжаю');
  }
}

async function devtoolsUrl(port, path, timeoutMs) {
  const t0 = Date.now();
  let last = '';
  for (;;) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}${path}`);
      if (r.ok) return await r.json();
      last = `HTTP ${r.status}`;
    } catch (e) { last = String(e && e.message ? e.message : e); }    if (Date.now() - t0 > timeoutMs) throw new Error(`DevTools не поднялся: ${last}`);
    await sleep(100);
  }
}

// --------------------------------------------------------------------------- //
// Клиент протокола DevTools
// --------------------------------------------------------------------------- //
// Потолок ожидания ответа: молчащий Chrome (убили, упал рендерер) иначе повесил бы
// съёмку навсегда — кадров нет, ошибки нет, Python ждёт.
const CALL_TIMEOUT_MS = 120000;

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.waits = new Map(); this.on = new Map(); }

  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => {
      ws.addEventListener('open', () => res(), { once: true });
      ws.addEventListener('error', (e) => rej(new Error('WebSocket: ' + (e.message || 'ошибка'))), { once: true });
    });
    const c = new CDP(ws);
    ws.addEventListener('message', (ev) => c._message(ev.data));
    // Ошибку соединения после установки никто не слушал: у WebSocket без обработчика
    // 'error' отказ становится необработанным исключением — процесс выходил кодом 1
    // без внятной причины (ровно это и выглядело как «съёмщик упал» в конце прогона).
    ws.addEventListener('error', () => c._fail(new Error('WebSocket: соединение с браузером оборвалось')));
    ws.addEventListener('close', () => c._fail(new Error('WebSocket: браузер закрыл соединение')));
    return c;
  }

  _fail(e) {
    for (const [, w] of this.waits) w.rej(e);
    this.waits.clear();
  }

  _message(raw) {
    let msg = null;
    try { msg = JSON.parse(typeof raw === 'string' ? raw : raw.toString()); } catch { return; }
    if (msg.id != null) {
      const w = this.waits.get(msg.id);
      if (!w) return;
      this.waits.delete(msg.id);
      clearTimeout(w.timer);
      if (msg.error) w.rej(new Error(msg.error.message || 'ошибка DevTools'));
      else w.res(msg.result || {});
      return;
    }
    const fns = this.on.get(msg.method);
    if (fns) for (const f of fns) { try { f(msg.params || {}); } catch { /* чужой обработчик не должен ломать съёмку */ } }
  }

  send(method, params) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      const timer = setTimeout(() => {
        this.waits.delete(id);
        rej(new Error(`${method}: браузер не ответил за ${CALL_TIMEOUT_MS / 1000} с`));
      }, CALL_TIMEOUT_MS);
      this.waits.set(id, { res, rej, timer });
      this.ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
  }

  listen(method, fn) {
    if (!this.on.has(method)) this.on.set(method, []);
    this.on.get(method).push(fn);
  }

  close() { try { this.ws.close(); } catch { /* уже закрыт */ } }
}

async function evaluate(cdp, expression) {
  const r = await cdp.send('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true,
  });
  if (r.exceptionDetails) {
    const d = r.exceptionDetails;
    throw new Error('страница: ' + ((d.exception && d.exception.description) || d.text || 'исключение'));
  }
  return r.result ? r.result.value : undefined;
}

async function waitReady(cdp, timeoutMs) {
  // Часы — монотонные: по календарным перевод системного времени на середине ожидания
  // давал «не готова за 180 с» через секунду после старта страницы.
  const t0 = performance.now();
  for (;;) {
    const v = await evaluate(cdp, 'window.reelsiRenderError||(window.reelsiRenderReady?"ready":(document.readyState||""))');
    if (v === 'ready') return;
    if (v && v !== 'loading' && v !== 'interactive' && v !== 'complete' && v !== 'uninitialized') {
      throw new Error('страница рендера: ' + v);
    }
    if (performance.now() - t0 > timeoutMs) {
      throw new Error(`страница рендера не готова за ${Math.round(timeoutMs / 1000)} с`);
    }
    await sleep(100);
  }
}

// --------------------------------------------------------------------------- //
// Вывод кадра: 4 байта длины (старший вперёд) + данные
// --------------------------------------------------------------------------- //
// Кадр: 4 байта длины (старший вперёд) + данные. write() отдаёт false, когда буфер
// трубы полон — это не отказ, а «подожди слива»: без ожидания Python получил бы
// обрезанный кадр ровно на медленном диске.
function writeAll(buf) {
  return new Promise((res, rej) => {
    try {
      if (process.stdout.write(buf)) return res();
      process.stdout.once('drain', () => res());
      process.stdout.once('error', rej);
    } catch (e) { rej(e); }
  });
}

function writeFrame(buf) {
  const head = Buffer.alloc(4);
  head.writeUInt32BE(buf.length, 0);
  return writeAll(head).then(() => writeAll(buf));
}

function flushStdout() {
  return new Promise((res) => process.stdout.write('', () => res()));
}

// --------------------------------------------------------------------------- //
// Съёмка
// --------------------------------------------------------------------------- //
// Счётчики времени в миллисекундах: seek — ожидание кадра <video> (внутри страницы),
// paint — отрисовка кадра (страница + сведение слоя), shot — captureScreenshot,
// write — отдача байт в stdout. Печатаются средним за TIMING_EVERY кадров.
function newStats() { return { frames: 0, seek: 0, paint: 0, shot: 0, write: 0 }; }

function resetStats(s) { s.frames = 0; s.seek = 0; s.paint = 0; s.shot = 0; s.write = 0; }

// Времена seek/paint считает СТРАНИЦА (window.reelsiRenderPaint, см. 85-inserts-view.js)
// и отдаёт СЕКУНДАМИ; здесь они переводятся в миллисекунды. Идут они в разбивку вместе
// со «своим» временем съёмщика (круг до браузера, JSON, base64): разбивка обязана
// покрывать кадр ЦЕЛИКОМ, иначе по ней нельзя решить, что ускорять. Страницы нет или
// дверь не ответила — нули: съёмку это не ломает, а в разбивке видно «остальное».
function readPaint(painted) {
  const o = (painted && typeof painted === 'object') ? painted : {};
  return { seek: (Number(o.seek) || 0) * 1000, paint: (Number(o.paint) || 0) * 1000 };
}

async function main() {
  const exe = chromePath(args.chrome && args.chrome !== true ? String(args.chrome) : '');
  const port = await freePort();
  chrome = await launchChrome(port, exe);
  await devtoolsUrl(port, '/json/version', 30000);
  const targets = await devtoolsUrl(port, '/json/list', 30000);
  const page = (targets || []).find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  if (!page) throw new Error('в Chrome не нашлось страницы для съёмки');
  const cdp = await CDP.connect(page.webSocketDebuggerUrl);

  cdp.listen('Runtime.exceptionThrown', (p) => {
    const d = p.exceptionDetails || {};
    err('#ошибка страницы: ' + ((d.exception && d.exception.description) || d.text || 'исключение'));
  });
  cdp.listen('Runtime.consoleAPICalled', (p) => {
    if (p.type !== 'error') return;
    const txt = (p.args || []).map((a) => (a.value != null ? a.value : a.description || '')).join(' ');
    err('#консоль: ' + txt);
  });

  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  // Чем рисует браузер — в лог ДО съёмки: это самая дорогая часть кадра, и по строке
  // видно, досталась рендеру карта или программный запасной путь.
  await reportGpu(cdp);
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: W, height: H, deviceScaleFactor: 1, mobile: false,
    screenWidth: W, screenHeight: H,
  });
  await cdp.send('Page.navigate', { url: URL_PAGE });
  await waitReady(cdp, READY_MS);

  const info = await evaluate(cdp, 'window.reelsiRenderInfo||null');
  const rect = await evaluate(cdp,
    'JSON.stringify((function(){var r=document.getElementById("ipvstage").getBoundingClientRect();'
    + 'return {x:r.left,y:r.top,w:r.width,h:r.height};})())');
  const box = JSON.parse(rect || '{}');
  if (!(box.w > 0) || !(box.h > 0)) throw new Error('сцена рендера пустая: ' + rect);
  // Сцена обязана стоять в натуральном размере кадра: снимок идёт один в один, без пересчёта.
  if (Math.abs(box.w - W) > 0.5 || Math.abs(box.h - H) > 0.5) {
    throw new Error(`сцена ${box.w}x${box.h} не равна кадру ${W}x${H}`);
  }
  err(`#ready ${W}x${H}@${FPS}${info && info.dur ? ' dur=' + info.dur : ''}`);

  const clip = { x: box.x, y: box.y, width: W, height: H, scale: 1 };
  const stats = newStats();
  for (let i = 0; i < FRAMES; i++) {
    const k = START + i;
    const t = k / FPS;
    // Кадр рисует ТОТ ЖЕ код предпросмотра — ждём, пока он выставит сцену на t.
    // Времена seek/paint считает СТРАНИЦА (window.reelsiRenderPaint, 85-inserts-view.js)
    // и отдаёт секундами: рядом с кадром они читаются, обнуляются и попадают в разбивку.
    // «Остальное» (JSON, протокол, круг до браузера) видно там же — оно не теряется,
    // а складывается с seek: разбивка по-прежнему покрывает весь кадр целиком.
    const tDraw = performance.now();
    const drawn = await evaluate(cdp, `ipvRenderAt(${t.toFixed(6)})`);
    if (drawn == null) throw new Error('сцена рендера не отрисовалась: кадр ' + k);
    const tPaint = performance.now();
    const painted = await evaluate(cdp, '(window.reelsiRenderPaint?window.reelsiRenderPaint():null)');
    const tShot = performance.now();
    const parts = readPaint(painted);
    stats.seek += (tPaint - tDraw) + parts.seek;      // ожидание кадра <video> + круг
    stats.paint += (tShot - tPaint) + parts.paint;    // отрисовка кадра + сведение слоя
    const shot = await cdp.send('Page.captureScreenshot',
      { format: FORMAT, quality: JPEG_Q, clip, fromSurface: true });
    if (!shot || !shot.data) throw new Error('снимок кадра пустой: ' + k);
    stats.shot += performance.now() - tShot;
    const tWrite = performance.now();
    await writeFrame(Buffer.from(shot.data, 'base64'));
    stats.write += performance.now() - tWrite;
    stats.frames++;
    err(`кадр ${i + 1}/${FRAMES}`);
    if (stats.frames % TIMING_EVERY === 0) {
      err(timingLine(stats));
      resetStats(stats);
    }
  }
  if (stats.frames > 0) err(timingLine(stats));    // хвост, не кратный TIMING_EVERY
  err(`#done ${FRAMES}`);
  cdp.close();
  killChrome();
  await flushStdout();          // хвост stdout дописан — мёртвый Chrome уже не держит цикл
}

// Успех — РОВНО ноль. Код выхода ставит завершение main: пока он не завершился, ни
// «готово», ни «упало» ещё не известно, а уборка (гашение Chrome и слив stdout) идёт
// ДО него — иначе последние кадры могли остаться в буфере, а код выхода соврать.
main().then(() => {
  killChrome();
  flushStdout().finally(() => process.exit(0));
}).catch(async (e) => {
  err('#ошибка: ' + (e && e.message ? e.message : e));
  killChrome();
  try { await flushStdout(); } catch { /* труба уже закрыта */ }
  process.exit(1);
});
