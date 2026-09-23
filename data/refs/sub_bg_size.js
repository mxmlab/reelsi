// SPDX-License-Identifier: AGPL-3.0-or-later
// Эталон из adcut.aep (ролик 01_IMG_2420): выражение на «Размер прямоугольника»
// плашки под субтитрами. Снято из проекта пользователя, менять смысл нельзя —
// подставляются только четыре константы в шапке.
// --- НАСТРОЙКИ ---
const fixedHeight  = 160;   // Высота плашки (px)
const padPercent   = 0.18;  // Отступ по бокам (~18% на сторону)
const minPadX      = 70;    // Минимальный отступ слева/справа (px)
const animDuration = 0.22;  // Длительность перехода (сек)

// Функция получения ширины: активной либо последней завершившейся фразы
function getRawWidth(t) {
  const precompLayer = thisComp.layer("Субтитры (текст)");
  const targetComp = comp(precompLayer.source.name);
  const localTime = precompLayer.sourceTime(t);

  let minX = Infinity;
  let maxX = -Infinity;
  let found = false;

  // 1. Проверяем активный текст в текущий момент времени
  for (let i = 1; i <= targetComp.numLayers; i++) {
    const l = targetComp.layer(i);
    if (localTime >= (l.inPoint - 0.01) && localTime < l.outPoint && l.enabled) {
      try {
        const r = l.sourceRectAtTime(localTime, false);
        if (r.width > 0) {
          const topLeft = l.toComp([r.left, r.top], localTime);
          const bottomRight = l.toComp([r.left + r.width, r.top + r.height], localTime);
          minX = Math.min(minX, topLeft[0], bottomRight[0]);
          maxX = Math.max(maxX, topLeft[0], bottomRight[0]);
          found = true;
        }
      } catch(err) {}
    }
  }

  if (found) return Math.max(0, maxX - minX);

  // 2. Если текста нет — берем размер последнего завершившегося блока (удержание размера)
  let lastOut = -Infinity;
  let lastIndices = [];

  for (let i = 1; i <= targetComp.numLayers; i++) {
    const l = targetComp.layer(i);
    if (l.enabled && l.outPoint <= localTime) {
      if (l.outPoint > lastOut + 0.05) {
        lastOut = l.outPoint;
        lastIndices = [i];
      } else if (Math.abs(l.outPoint - lastOut) <= 0.05) {
        lastIndices.push(i);
      }
    }
  }

  if (lastIndices.length > 0) {
    const sampleT = Math.max(0, lastOut - 0.02);
    for (const idx of lastIndices) {
      const l = targetComp.layer(idx);
      try {
        const r = l.sourceRectAtTime(sampleT, false);
        if (r.width > 0) {
          const topLeft = l.toComp([r.left, r.top], sampleT);
          const bottomRight = l.toComp([r.left + r.width, r.top + r.height], sampleT);
          minX = Math.min(minX, topLeft[0], bottomRight[0]);
          maxX = Math.max(maxX, topLeft[0], bottomRight[0]);
        }
      } catch(err) {}
    }
    if (minX !== Infinity) return Math.max(0, maxX - minX);
  }

  // 3. До старта первых субтитров — берем размер самого первого блока
  let firstIn = Infinity;
  let firstIndices = [];

  for (let i = 1; i <= targetComp.numLayers; i++) {
    const l = targetComp.layer(i);
    if (l.enabled) {
      if (l.inPoint < firstIn - 0.05) {
        firstIn = l.inPoint;
        firstIndices = [i];
      } else if (Math.abs(l.inPoint - firstIn) <= 0.05) {
        firstIndices.push(i);
      }
    }
  }

  if (firstIndices.length > 0) {
    const sampleT = firstIn + 0.02;
    for (const idx of firstIndices) {
      const l = targetComp.layer(idx);
      try {
        const r = l.sourceRectAtTime(sampleT, false);
        if (r.width > 0) {
          const topLeft = l.toComp([r.left, r.top], sampleT);
          const bottomRight = l.toComp([r.left + r.width, r.top + r.height], sampleT);
          minX = Math.min(minX, topLeft[0], bottomRight[0]);
          maxX = Math.max(maxX, topLeft[0], bottomRight[0]);
        }
      } catch(err) {}
    }
    if (minX !== Infinity) return Math.max(0, maxX - minX);
  }

  return 0;
}

function calcFullWidth(rawW) {
  if (rawW <= 0) return 0;
  return rawW + Math.max(minPadX * 2, rawW * (padPercent * 2));
}

// --- EASE АНИМАЦИЯ ---
const curW = calcFullWidth(getRawWidth(time));
const dt = thisComp.frameDuration;
const maxFrames = Math.ceil(animDuration / dt) + 1;

let prevW = curW;
let changeTime = time;

for (let f = 1; f <= maxFrames; f++) {
  const tCheck = time - f * dt;
  const wCheck = calcFullWidth(getRawWidth(tCheck));
  if (Math.abs(wCheck - curW) > 2) {
    prevW = wCheck;
    changeTime = tCheck + dt;
    break;
  }
}

const progress = Math.min(1, Math.max(0, (time - changeTime) / animDuration));
const easeVal = 1 - Math.pow(1 - progress, 4); // Ease Out Quart

const finalWidth = prevW + (curW - prevW) * easeVal;

[finalWidth, fixedHeight];