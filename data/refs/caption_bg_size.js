// SPDX-License-Identifier: AGPL-3.0-or-later
// Эталон из adcut.aep: выражение на «Размер прямоугольника» плашки под подписью
// о ролике. Плашка тянется за текстом слоя textlayer1.
const targetLayerName = "textlayer1"; // Имя слоя с текстом
const txtLayer = thisComp.layer(targetLayerName);

// Удерживаем размеры за пределами диапазона слоя
const sampleT = Math.min(Math.max(time, txtLayer.inPoint), Math.max(0, txtLayer.outPoint - 0.01));

try {
  const r = txtLayer.sourceRectAtTime(sampleT, false);
  [r.width * 0.9, r.height * 1.3]; // -10% ширины, +30% высоты
} catch (e) {
  [0, 0];
}