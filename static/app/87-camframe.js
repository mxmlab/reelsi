// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// Рамка кадра камеры: какая часть исходника попадает в кадр ролика.
//
// Обрезка по умолчанию — по центру: исходник (горизонтальный, вертикальный, любой)
// вписывается в кадр с обрезкой, и лишнее срезается поровну. Рамка задаёт это числами:
// x/y — точка исходника (доли кадра), которая встаёт в центр кадра ролика, zoom —
// проценты от «кадр заполнен ровно».
//
// Хранится в профиле спикера полем `frame` рядом с LUT и форматом кадра — по камерам
// ({"1": {...}, "2": {...}}), как LUT. Камеры в поле нет или значения 0.5/0.5/100 —
// обрезка по центру, как была: профиль без правок не меняет ничего.
//
// Мышкой рамка больше не правится: режим «Рамка камеры» из превью убран — кадр и зум
// камеры живут в СТИЛЕ (cam1_fit, cam1_zoom_cx/cy, cam1_pan_x/y — core/style_schema.py,
// группа «Transform» Камеры 1; ссылка в редакторе спикера ведёт туда). Здесь остаётся
// сам движок рамки: поле профиля, отрисовка кадра превью и сборка. Дефолтной рамки
// никто не заводит — у профилей без поля `frame` не меняется ничего.
//
// Числа обязаны совпасть с тем, что уедет в After Effects и в Premiere, поэтому
// формулы здесь — ЗЕРКАЛО core/frame.py (там единственный источник правила и
// единственный зажим). Совпадение чисел стережёт тест: tests/test_cam_frame.py
// гоняет ЭТИ функции node'ом, копий формул в тесте нет.
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами (не модулями): один общий скоуп, как было в едином app.js.

const CAMFRAME_MIN = 100, CAMFRAME_MAX = 400;      // zoom, % (core/frame.py: FRAME_ZOOM_*)
const CAMFRAME_DEF = { x: 0.5, y: 0.5, zoom: 100 };

// ---- числа рамки: зеркало core/frame.py ----
function camFrameNorm(raw){const f=raw||{};
  const num=(v,d)=>{const x=parseFloat(v);return isFinite(x)?x:d;};
  return {x:Math.max(0,Math.min(1,num(f.x,CAMFRAME_DEF.x))),
          y:Math.max(0,Math.min(1,num(f.y,CAMFRAME_DEF.y))),
          zoom:Math.max(CAMFRAME_MIN,Math.min(CAMFRAME_MAX,num(f.zoom,CAMFRAME_DEF.zoom)))};}

function camFrameDefault(f){const n=camFrameNorm(f);
  return n.x===CAMFRAME_DEF.x&&n.y===CAMFRAME_DEF.y&&n.zoom===CAMFRAME_DEF.zoom;}

// Кусок ИСХОДНИКА (px), который попадает в кадр: [x, y, w, h]. Размер куска — доля
// 1/zoom от «кадр заполнен ровно» и всегда с пропорциями кадра ролика; центр куска
// зажат в границы исходника — показать то, чего в исходнике нет, нельзя, и это же
// зажимание и есть «кадр всегда заполнен» (при 100% и равных пропорциях сдвига нет).
function camFrameCrop(vw,vh,W,H,fr){
  const f=camFrameNorm(fr),z=f.zoom/100,fc=Math.max(W/vw,H/vh);
  const cw=Math.min(vw,W/(fc*z)),ch=Math.min(vh,H/(fc*z));
  const cx=Math.min(Math.max(f.x*vw,cw/2),vw-cw/2);
  const cy=Math.min(Math.max(f.y*vh,ch/2),vh-ch/2);
  return [cx-cw/2,cy-ch/2,cw,ch];}

// Сдвиг слоя камеры в пикселях КАДРА — то же, что Basic Motion > Center в Premiere и
// Position слоя в AE. Считается ИЗ куска: на сколько его центр ушёл от центра исходника.
function camFrameShift(vw,vh,W,H,fr){
  const f=camFrameNorm(fr),z=f.zoom/100,fc=Math.max(W/vw,H/vh);
  const c=camFrameCrop(vw,vh,W,H,f);
  return [(vw/2-(c[0]+c[2]/2))*fc*z,(vh/2-(c[1]+c[3]/2))*fc*z];}

// Множитель масштаба слоя камеры от рамки: 1 — кадр заполнен ровно.
function camFrameZoom(fr){return camFrameNorm(fr).zoom/100;}

// ---- что за клип открыт и какая у него рамка ----
function camFramePlan(){return (typeof IPV!=='undefined'&&IPV.plan)?IPV.plan:null;}
function camFrameDim(){const pl=camFramePlan();return {W:(pl&&pl.w)||1080,H:(pl&&pl.h)||1920};}

// Клип открытого предпросмотра: и спикер, и его рамка — у СВОЕГО клипа (как у LUT,
// 86-lut.js): предпросмотр мог остаться открытым от другого ролика.
function camFrameClip(){return (typeof lutClip==='function')?lutClip():null;}
function camFrameSpeaker(){const c=camFrameClip();return (c&&c.job&&c.job.speaker)||'';}
function camFrameProfile(){const k=camFrameSpeaker();
  return (k&&typeof SPEAKERS!=='undefined'&&SPEAKERS[k])||null;}

// Кадр ролика (w, h) — формат из профиля спикера ЭТОГО клипа: тот же путь, что у
// рамки кадра камеры. Размеры форматов приезжают с /api/speakers из core/frame.py
// (SPKFORMATS): значений «1080×1080» в интерфейсе нет — вторая копия разошлась бы
// с бэкендом молча. Профиля нет, формат пустой или незнакомый — 9:16, как раньше.
function camFrameWH(){
  const p=camFrameProfile();
  const f=(p&&p.format)||'';
  const wh=((typeof SPKFORMATS!=='undefined'&&SPKFORMATS)||{})[f]
    ||((typeof SPKFORMATS!=='undefined'&&SPKFORMATS)||{})['9:16']||[1080,1920];
  return wh;}
// Множители осей «базовые единицы стиля -> px кадра этого клипа». Ими драг
// переводит экранные пиксели обратно в базовые: иначе перетащил на квадрате, а
// в вертикали уехало (кадр у них разной высоты). Формулу берём у пересчёта стиля
// (stScaleKind, static/app/95-styles.js) — она одна на весь фронт, а таблица
// «поле -> вид» и поле-представитель вида приезжают из схемы панели: второй копии
// правила здесь нет.
function camFrameAxisK(){const wh=camFrameWH();const w=(wh&&wh[0])||1080,h=(wh&&wh[1])||1920;
  return {x:stScaleKind('x',w,h),y:stScaleKind('y',w,h)};}

// Рамка камеры `ci` (индекс с 0) из профиля спикера; нет — дефолт (обрезка по центру).
function camFrameOf(ci){
  const p=camFrameProfile();
  const raw=(p&&p.frame&&p.frame[String(ci+1)])||null;
  return camFrameNorm(raw);}
