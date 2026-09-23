// SPDX-License-Identifier: AGPL-3.0-or-later
// Выражение на позицию плашки под подписью — центрирование по тексту.
const targetLayerName = "Подпись";
const txtLayer = thisComp.layer(targetLayerName);
const sampleT = Math.min(Math.max(time, txtLayer.inPoint), Math.max(0, txtLayer.outPoint - 0.01));
try {
  const r = txtLayer.sourceRectAtTime(sampleT, false);
  txtLayer.toComp([r.left + r.width / 2, r.top + r.height / 2], sampleT);
} catch (e) {
  value;
}
