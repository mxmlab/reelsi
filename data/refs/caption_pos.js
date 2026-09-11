// SPDX-License-Identifier: AGPL-3.0-or-later
// Выражение на позицию ТЕКСТА подписи: caption_x — левый край блока (плашки),
// поэтому центр надписи = левый край + половина ширины плашки. Ширину плашки
// AE знает только в момент отрисовки, отсюда выражение (жалоба «подпись должна
// начинаться слева», 2026-08-20). Константы CAP_X / CAP_Y / KX подставляет Python.
const CAP_X = 179, CAP_Y = 228, KX = 1.718;
const r = thisLayer.sourceRectAtTime(Math.max(time, inPoint), false);
[CAP_X + r.width * KX / 2, CAP_Y];
