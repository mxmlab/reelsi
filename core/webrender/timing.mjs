// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
//
// Разбивка времени съёмки кадров — ОДНА на печать и на разбор.
//
// Строку печатает съёмщик (capture.mjs), разбирает Python (core.webrender.TIMING_RE) и
// сверяет тест (tests/test_webrender.py). Живой прогон у владельца показал 1.3 с на
// кадр — без разбивки не видно, на что именно уходит время: ждать кадр <video>,
// рисовать, снимать или отдавать байты. Поэтому формат обязан быть один: разошедшаяся
// строка молча превратилась бы в «разбивки нет» ровно тогда, когда она нужна.
//
// Формат (единицы — МИЛЛИСЕКУНДЫ, среднее за окно):
//     #timing seek=12.3 paint=4.5 shot=980.1 write=2.0 (мс, среднее)
// `seek` ждёт кадр <video> (и круг до браузера), `paint` отрисовывает кадр,
// `shot` снимает его протоколом DevTools, `write` отдаёт байты в stdout.

// Сколько кадров накапливается до печати строки. Тридцать — как у прогресса «кадр n/N»:
// чаще печатать нечего (числа успевают измениться), реже — теряется картина по ходу.
export const TIMING_EVERY = 30;

// Округление до десятых: миллисекунды, а не микросекунды — у съёмки разброс больше.
const round = (ms) => (Math.round(Number(ms) * 10) / 10).toFixed(1);

/**
 * Строка разбивки по счётчикам окна. `s.frames` — сколько кадров в окне.
 * @param {{frames: number, seek: number, paint: number, shot: number, write: number}} s
 * @returns {string}
 */
export function timingLine(s) {
  const n = Number(s && s.frames) || 0;
  if (n <= 0) return '';
  const avg = (v) => round((Number(v) || 0) / n);
  return `#timing seek=${avg(s.seek)} paint=${avg(s.paint)} shot=${avg(s.shot)} `
    + `write=${avg(s.write)} (мс, среднее)`;
}
