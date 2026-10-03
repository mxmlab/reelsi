# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Звук ролика для рендера без After Effects: микс по плану сцены.

Картинку рендер без AE берёт с нашего же предпросмотра (`core/webrender.py`), а звук
собирается здесь — и он тоже ЦЕЛИКОМ из плана сцены: файл голоса, куски, громкости
`voice_db`/`music_db`, окна цензуры и события звуков (`plan["audio"]`, там же, где их
берёт `.jsx`). Ни одного своего правила «как микшировать» мимо плана: разойтись с AE
микс может только там, где план и сам молчит.

Что в AE делает шаблон (`core/xml2ae/template.py`) и как это повторено:

| В AE (`.jsx`)                                     | В миксе                                           |
|---------------------------------------------------|---------------------------------------------------|
| аудиослой на каждый клип камеры 1: `startTime`,    | те же куски по `plan.audio.segments` (`atrim` +   |
| `inPoint`, `outPoint`; звук камер 2 выключен       | `adelay`), камера 2 не звучит вообще              |
| `Audio Levels` клипа = `VOICE_DB`                  | `volume=<voice_db>dB` на каждом куске             |
| микро-фейд `AUDIO_FADE` по краям клипа, если       | `afade` на куске, `AUDIO_FADE` из плана           |
| `(out-in) > 4*AUDIO_FADE`                          | (условие то же)                                   |
| `censorLayer`: `VOICE_DB` → −100 → −100 →          | те же окна, те же рампы 0.02 с, дно −100 dB       |
| `VOICE_DB`, рампа 0.02 с до и после окна           | (`_censor_pieces`, ступенями)                     |
| музыка отдельным слоем `startTime=0`, `MUSIC_DB`   | `music_path` от нуля, `volume=<music_db>dB`, а в  |
|                                                    | окне рендера — с той же секунды (`start`)         |
| звуки `POP/RISER/TRANS_SFX`: слой на событие,      | события `plan.audio.sfx` с готовым `t`, конец     |
| `outPoint` из стиля (у «попа» 0.1 с), громкость    | `out` из плана (тот же 0.1 с у «попа»), нет       |
| `base+db`; у глитча — огибающая                    | `out` — до конца файла; громкость `base+db`       |

Чего в миксе НЕТ:
* **звук видеоперехода** (`kind == "transition"`): в AE это видеофайл Quick 2.mov, чей
  звук идёт вместе с картинкой перехода, а клипы камер в рендере без AE склеены встык —
  звук перехода повис бы над стыком. Whoosh того же перехода (свой файл) звучит;
* **огибающая звука глитча**: у слоя `.jsx` она ступенями — тишина до первого слова
  группы, 0.08 с нарастания, полка, 0.12 с спада (`GLITCH_SFX_*`), — а в плане её нет
  вовсе: и превью, и микс играют событие постоянной громкостью `base+db`. Взять
  огибающую из шаблона значило бы завести своё правило мимо плана, а план читает ещё и
  предпросмотр;
* **motion blur и прочее, чего нет в плане** — не из плана, значит и не здесь.

Окно рендера (`start`/`dur`) сдвигает ВСЁ, как кадр композиции: голос входит в исходник
со своей секунды (`segments[].src`), музыка и звуки — со своей (`atrim=start=`), а
событие, начавшееся до окна, играет со своей середины.

Почему ffmpeg, а не своё сведение: голос, музыка и звуки — три дорожки, каждая со своей
громкостью и своими окнами, и ffmpeg же кодирует итог в AAC 48 кГц. Своя арифметика
сэмплов разошлась бы с AE молча.

Как устроен голос. Он режется на КУСКИ плана (`segments`) и на ступени цензуры
(`_censor_pieces`), каждый кусок получает свою громкость в dB и своё место в ролике
(`adelay`), после чего всё сводится `amix` на тишину нужной длины. Так сделано после
того, как две «умные» двери ffmpeg оказались негодными, и это проверено замером:
* `aevalsrc` + `amultiply` — длинное вложенное выражение огибающей `aevalsrc` разбирает
  неверно (уровень оставался прежним), а `min`/`max` в нём не зажимают значение по краям;
* `volume` с `enable` по времени — на простом графе окно глушит, а на боевом (несколько
  кусков, `amix`) ступени в окне давали −0.2 dB вместо −100 (замер).

Ступени вместо рампы: рампа 20 мс делится на `CENSOR_RAMP_STEPS` отрезков с постоянной
громкостью. На слух это то же самое (щелчка нет), но граф остаётся из `atrim`/`volume`/
`adelay`/`amix` — фильтров без выражений и без опций времени, которые нельзя
перепроверить тестом на числах.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Callable, Sequence

from core.app_meta import wrap_emit
from core.fileio import atomic_text_write
from core.jobstate import task_popen_kwargs
from core.umsg import ReelsiError, umsg

SAMPLE_RATE = 48000              # частота итогового AAC — как у всего проекта
CHANNELS = 2                     # стерео: голос, музыка и звуки сводятся в общий микс
AUDIO_BITRATE = "192k"
# Дно цензуры: −100 dB — ровно то, во что ныряет `censorLayer` в шаблоне. Тишина
# «в ноль» не годится: у AE и у нас это одно и то же число, и оно проверяется тестом.
VOICE_SILENT_DB = -100.0
# Рампа цензуры: в шаблоне окно окружено 0.02 с возврата к VOICE_DB (`a-0.02` / `b+0.02`).
CENSOR_RAMP_S = 0.02
# На сколько ступеней делится рампа: 20 мс / 5 = 4 мс на ступень, на слух неотличимо от
# линейной рампы, а граф остаётся из простых фильтров.
CENSOR_RAMP_STEPS = 5
# Имя временного файла с графом фильтров рядом с итоговым видео: убирает вызывающий.
TMP_PREFIX = ".reelsi_mix_"
# Точность времени в графе: миллисекунда — это меньше кадра даже на 120 к/с.
_TIME_FMT = "%.6f"


def _db2lin(db: Any) -> float:
    """dB → линейный множитель (то же, что `dbToGain` в превью и `volume` в ffmpeg)."""
    try:
        v = float(db)
    except (TypeError, ValueError):
        return 1.0
    return float(10.0 ** (v / 20.0))


def _num(v: Any, default: float = 0.0) -> float:
    """Число из плана: None/мусор — дефолт (план может не знать поля вовсе)."""
    if isinstance(v, bool) or v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fmt(v: float) -> str:
    """Число для фильтра ffmpeg: без экспоненты.

    Питон пишет `1e-05` для мелких чисел, а парсер фильтров экспоненту в значениях не
    понимает — миллисекундные длины превратились бы в «Invalid argument» на всём графе.
    """
    s = ("%.6f" % float(v))
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _t(v: float) -> str:
    """Время в графе — шестью знаками: миллисекунда меньше кадра даже на 120 к/с."""
    return _TIME_FMT % max(0.0, float(v))


def _window(plan: dict[str, Any], start: float, dur: float) -> tuple[float, float]:
    """Окно рендера в монтажном времени: (начало, конец), конец не раньше начала."""
    t0 = max(0.0, float(start or 0.0))
    span = float(dur or 0.0) or max(0.0, _num(plan.get("dur")) - t0)
    return t0, t0 + max(0.0, span)


def _pieces(segments: Sequence[dict[str, Any]], windows: Sequence[Any], t0: float,
            t1: float) -> list[tuple[float, float, float, float]]:
    """Куски голоса со своей громкостью: [(задержка, вход, длина, dB)].

    Куски плана (`plan["audio"]["segments"]`) режутся окном рендера, а затем — окнами
    цензуры: рампа 0.02 с превращается в `CENSOR_RAMP_STEPS` ступеней постоянной
    громкости. Куски вне окон цензуры получают `voice_db`, ступени — значения между
    `voice_db` и `VOICE_SILENT_DB`, полка окна — `VOICE_SILENT_DB`.
    """
    spans: list[tuple[float, float, float]] = []
    for seg in segments or []:
        ts, te = _num(seg.get("ts")), _num(seg.get("te"))
        if te <= ts or te <= t0 or ts >= t1:
            continue
        a, b = max(ts, t0), min(te, t1)
        # Вход в исходник В НАЧАЛЕ ОКНА: кусок плана играет с `src`, и окно рендера
        # сдвигает точку входа на столько же, на сколько сдвинуло начало куска.
        spans.append((a, b, _num(seg.get("src")) + (a - ts)))
    # Разрезы: границы кусков и края окна цензуры с рампами (шаг ступени).
    cuts: set[float] = set()
    for a, b, _src in spans:
        cuts.add(a)
        cuts.add(b)
    for w in windows or []:
        if not w or len(w) < 2:
            continue
        lo, hi = _num(w[0]), _num(w[1])
        if hi <= lo:
            continue
        step = CENSOR_RAMP_S / CENSOR_RAMP_STEPS
        for k in range(CENSOR_RAMP_STEPS + 1):
            cuts.add(lo - CENSOR_RAMP_S + k * step)
            cuts.add(hi + k * step)
    grid = sorted(c for c in cuts if t0 <= c <= t1)
    out: list[tuple[float, float, float, float]] = []
    for a, b, src0 in spans:
        inner = [c for c in grid if a < c < b]
        bounds = [a, *inner, b]
        for lo, hi in zip(bounds, bounds[1:]):
            length = hi - lo
            if length <= 1e-6:
                continue
            out.append((lo - t0, src0 + (lo - a), length, 0.0))
    return out


def _gain_at(t: float, windows: Sequence[Any], voice_db: float) -> float:
    """Громкость голоса (dB) в момент `t`: `voice_db`, дно окна или ступень рампы.

    Ровно то, что делает `censorLayer` в шаблоне: `VOICE_DB` вне окна, −100 dB внутри и
    линейный переход за 0.02 с до и после. Ступенями — потому что `volume` в ffmpeg
    умеет одну громкость на кусок (см. шапку модуля).
    """
    for w in windows or []:
        if not w or len(w) < 2:
            continue
        lo, hi = _num(w[0]), _num(w[1])
        if hi <= lo:
            continue
        if lo <= t <= hi:
            return VOICE_SILENT_DB
        if lo - CENSOR_RAMP_S < t < lo:
            k = (lo - t) / CENSOR_RAMP_S                    # 0 → 1 к началу окна
            return voice_db + (VOICE_SILENT_DB - voice_db) * k
        if hi < t < hi + CENSOR_RAMP_S:
            k = (t - hi) / CENSOR_RAMP_S                    # 0 → 1 от конца окна
            return VOICE_SILENT_DB + (voice_db - VOICE_SILENT_DB) * k
    return voice_db


def censored_pieces(segments: Sequence[dict[str, Any]], windows: Sequence[Any],
                    voice_db: float, t0: float, t1: float
                    ) -> list[tuple[float, float, float, float]]:
    """Куски голоса со своей громкостью: [(задержка, вход, длина, dB)] — окно рендера.

    Громкость берётся из `_gain_at` ОДИН раз на кусок: второй копии правила «сколько
    играть в этот момент» в графе не остаётся — в граф уезжают готовые числа.
    """
    out: list[tuple[float, float, float, float]] = []
    for delay, src_in, length, _unused in _pieces(segments, windows, t0, t1):
        mid = t0 + delay + length / 2.0
        out.append((delay, src_in, length, _gain_at(mid, windows, voice_db)))
    return out


def _sfx_events(audio: dict[str, Any]) -> list[dict[str, Any]]:
    """События звуков из плана: по возрастанию времени, видеопереход — вон.

    Видеопереход в миксе не звучит: в AE это отдельный видеофайл, чей звук идёт вместе с
    картинкой перехода, а клипы камер здесь склеены встык — звук повис бы над стыком.
    """
    out = [s for s in (audio.get("sfx") or [])
           if str(s.get("kind") or "") != "transition"]
    out.sort(key=lambda s: min([_num(e.get("t")) for e in (s.get("events") or [])] or [0.0]))
    return out


def build_graph(plan: dict[str, Any], *, start: float = 0.0,
                dur: float = 0.0) -> tuple[str, list[str]]:
    """Граф фильтров по плану: (тело фильтра, входные файлы ПО ПОРЯДКУ).

    Первый вход графа — голос (`[0:a]`), дальше музыка и файлы звуков; ровно в этом
    порядке их надо подать ffmpeg (см. `inputs`). Один и тот же файл берётся ОДИН раз:
    у «попа» на каждое жёлтое слово это десятки событий, и десятки входов заставляли бы
    ffmpeg открывать один файл многократно.

    Пустой список входов — «звука нет вовсе» (ни голоса, ни музыки, ни звуков): тогда и
    графа нет, рендер оставляет видео как есть.
    """
    audio = plan.get("audio") or {}
    t0, t1 = _window(plan, start, dur)
    span = max(0.0, t1 - t0)
    voice_db = _num(audio.get("voice_db"))
    fade = max(0.0, _num(plan.get("audio_fade")))
    voice = str(audio.get("voice_src") or "")
    segs = list(audio.get("segments") or [])
    windows = [w for w in (audio.get("censor") or []) if w and len(w) >= 2]
    # Кусков голоса нет и без файла голоса: пустой `voice_src` — это ролик без камеры 1,
    # и падать на «нет файла» здесь не на чем (музыка и звуки сведутся и без голоса).
    pieces = censored_pieces(segs, windows, voice_db, t0, t1) if voice else []
    music = str(audio.get("music_path") or "")
    sfx_list = [s for s in _sfx_events(audio) if str(s.get("media") or "")]
    if not pieces and not music and not sfx_list:
        return "", []

    # Голос — первый вход и только когда он звучит: без кусков `[0:a]` в графе не
    # упоминается вовсе, а лишний вход заставлял бы ffmpeg открывать файл зря.
    inputs: list[str] = [voice] if pieces else []
    parts: list[str] = []
    # Голос: куски плана кладутся НА тишину нужной длины. Тишина-основа задаёт длину
    # микса (ровно окно рендера) и не даёт событиям за концом ролика его удлинить.
    parts.append("aevalsrc=exprs='0':s=%d:c=stereo:d=%s[vbase]"
                 % (SAMPLE_RATE, _t(span)))
    labels = ["[vbase]"]
    if pieces:
        # Один вход на кусок (файл один — значит `asplit`): у каждого своя копия потока,
        # и порядок чтения между ними не важен.
        parts.append("[0:a]aresample=%d,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     "asplit=%d%s" % (SAMPLE_RATE, len(pieces),
                                      "".join("[vo%d]" % k for k in range(len(pieces)))))
        for k, (delay, src_in, length, gain) in enumerate(pieces):
            f = ("[vo%(k)d]atrim=start=%(i)s:duration=%(d)s,asetpts=N/SR/TB,volume=%(g)sdB"
                 % {"k": k, "i": _t(src_in), "d": _t(length), "g": _fmt(gain)})
            # Микро-фейд — как в шаблоне: только у куска длиннее 4 фейдов (у короткого
            # фейд съел бы весь звук). Края окна цензуры уже заданы громкостью куска.
            if fade > 0 and length > 4 * fade:
                f += (",afade=t=in:st=0:d=%(f)s:curve=tri,"
                      "afade=t=out:st=%(o)s:d=%(f)s:curve=tri"
                      % {"f": _fmt(fade), "o": _fmt(length - fade)})
            if delay > 1e-6:
                f += ",adelay=delays=%d:all=1" % int(round(delay * 1000.0))
            parts.append(f + "[vp%d]" % k)
            labels.append("[vp%d]" % k)
        parts.append("".join(labels)
                     + "amix=inputs=%d:duration=first:normalize=0[vout]"
                     % len(labels))
    else:
        # Кусков нет (ролик без камеры 1): голос — та же тишина-основа, чтобы микс
        # сошёлся и музыка со звуками не удлинили ролик.
        parts.append("[vbase]anull[vout]")

    # ---- остальные дорожки: музыка и звуки, каждая в свой вход ----
    n = 1
    extra: list[str] = []
    if music:
        inputs.append(music)
        # Музыка в AE лежит ОТ НУЛЯ композиции: кусок ролика, снятый не с начала
        # (`start`), берёт музыку с той же секунды, а не с начала файла. Для рендера
        # целого ролика сдвиг нулевой — строки в графе не появляется.
        shift = "atrim=start=%s,asetpts=N/SR/TB," % _t(t0) if t0 > 1e-6 else ""
        extra.append("[%(i)d:a]aresample=%(r)d,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     "%(sh)svolume=%(g)sdB[mix%(n)d]"
                     % {"i": len(inputs) - 1, "r": SAMPLE_RATE, "sh": shift,
                        "g": _fmt(_num(audio.get("music_db"))), "n": n})
        n += 1
    by_media: dict[str, int] = {}
    for sfx in sfx_list:
        media = str(sfx["media"])
        if media not in by_media:
            inputs.append(media)
            by_media[media] = len(inputs) - 1
        idx = by_media[media]
        gain = _db2lin(_num(sfx.get("base")) + _num(sfx.get("db")))
        # Событие звука: файловые in/out, старт по готовому `t` из плана. Длина — тоже
        # из плана (`out - in`): у «попа» это базовая обрезка 0.1 с, у ризера и whoosh
        # конца нет вовсе — звучат до конца файла, как слой .jsx без outPoint.
        for ev in sfx.get("events") or []:
            t = _num(ev.get("t"))
            fin = _num(ev.get("in"))
            fout = ev.get("out")
            # Конец события — из плана. `None` — «до конца файла»: так звучат ризер и
            # whoosh, у которых outPoint в шаблоне не ставится. Обрезать нечем, а длину
            # микса всё равно держит тишина-основа (`amix duration=first`).
            ev_len: float | None = None if fout is None else (_num(fout) - fin)
            # Событие целиком вне окна — не звучит. Считаем по КОНЦУ события, а не по
            # началу: слой, начавшийся до окна и ещё звучащий в нём, из ролика не пропадает.
            if t >= t1 or (ev_len is not None and t + ev_len <= t0):
                continue
            # Началось ДО окна — играет со своей середины: в AE такой слой попадает в
            # кадр уже идущим, и начинать его сначала значило бы сдвинуть звук ровно на
            # столько, на сколько окно отстоит от начала события.
            lead = max(0.0, t0 - t)
            fin += lead
            delay = max(0.0, t - t0)
            trim = ""                     # не `dur`: так зовётся окно рендера у build_graph
            if ev_len is not None:
                ev_len = min(ev_len - lead, max(0.0, span - delay))  # за конец ролика не лезем
                if ev_len <= 0:
                    continue
                trim = ":duration=%s" % _t(ev_len)
            f = ("[%(i)d:a]atrim=start=%(in)s%(d)s,asetpts=N/SR/TB,"
                 "aresample=%(r)d,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                 "volume=%(g)s"
                 % {"i": idx, "in": _t(fin), "d": trim, "r": SAMPLE_RATE,
                    "g": _fmt(gain)})
            if delay > 1e-6:
                f += ",adelay=delays=%d:all=1" % int(round(delay * 1000.0))
            extra.append(f + "[mix%d]" % n)
            n += 1
    if extra:
        # `duration=first` — длина микса по голосу: события, чей файл длиннее ролика, не
        # удлиняют итог (в AE слой за конец композиции не выходит).
        parts.extend(extra)
        parts.append("[vout]" + "".join("[mix%d]" % k for k in range(1, n))
                     + "amix=inputs=%d:duration=first:normalize=0," % n
                     + "aresample=%d,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       "atrim=start=0:end=%s,asetpts=N/SR/TB[aout]"
                       % (SAMPLE_RATE, _t(span)))
    else:
        parts.append("[vout]aresample=%d,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                             "atrim=start=0:end=%s,asetpts=N/SR/TB[aout]"
                     % (SAMPLE_RATE, _t(span)))
    return ";\n".join(parts), inputs


def mix_command(plan: dict[str, Any], video: str, out: str, *, start: float = 0.0,
                dur: float = 0.0) -> list[str]:
    """Командная строка микса: входы по порядку графа, затем видео, затем склейка.

    Граф уезжает ФАЙЛОМ (`-filter_complex_script`): он длинный, и в командной строке
    упирался бы в её потолок. Видео уходит в итог БЕЗ перекодирования (`-c:v copy`):
    его уже собрал рендер кадров, второй раз кодировать его незачем.
    """
    _graph, inputs = build_graph(plan, start=start, dur=dur)
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for p in inputs:
        cmd += ["-i", p]
    cmd += ["-i", video, "-filter_complex_script", _graph_path(out),
            "-map", "%d:v:0" % len(inputs), "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
            "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-shortest", out]
    return cmd


def _graph_path(out: str) -> str:
    """Файл графа рядом с итоговым видео: имя одно на рендер, содержимое — своё."""
    return os.path.join(os.path.dirname(os.path.abspath(out)), TMP_PREFIX + "graph.txt")


def mix(plan: dict[str, Any], video: str, out: str, *, start: float = 0.0,
        dur: float = 0.0, emit: Callable[..., Any] | None = None,
        run: Callable[..., Any] | None = None) -> str | None:
    """Свести звук по плану и склеить с готовым видео. None — звука в плане нет.

    `run` — дверь запуска ffmpeg: тесты подменяют её, чтобы проверить граф, не гоняя
    кодеки. Возвращает путь итогового файла.
    """
    em = wrap_emit(emit)
    graph, inputs = build_graph(plan, start=start, dur=dur)
    if not inputs:
        em("звук: в плане нет ни голоса, ни музыки, ни звуков — видео без звуковой дорожки")
        return None
    missing = [p for p in inputs if not os.path.isfile(p)]
    if missing:
        # Файла нет — это не «тишина», а поломка входа: молча отдать ролик без звука
        # значит показать брак, о котором узнают только на просмотре.
        raise ReelsiError(umsg("webrender_audio_missing",
                               "Звук ролика не собрать: нет файла {path}",
                               path=os.path.basename(missing[0])))
    script = _graph_path(out)
    atomic_text_write(script, graph, encoding="utf-8")
    em("звук: голос {vb:g} dB, музыка {mb:g} dB, окон цензуры {c}, дорожек {n}",
       vb=_num(plan.get("audio", {}).get("voice_db")),
       mb=_num(plan.get("audio", {}).get("music_db")),
       c=len(plan.get("audio", {}).get("censor") or []), n=len(inputs))
    runner = run or subprocess.run
    try:
        p = runner(mix_command(plan, video, out, start=start, dur=dur),
                   capture_output=True, **task_popen_kwargs())
        code = int(getattr(p, "returncode", 0) or 0)
        if code != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
            err = (getattr(p, "stderr", b"") or b"").decode("utf-8", "replace").strip()
            raise ReelsiError(umsg("webrender_audio_ffmpeg",
                                   "Звук не собрался (код {code}): {err}",
                                   code=code, err="\n".join(err.splitlines()[-4:]) or "?"))
    finally:
        try:
            os.remove(script)
        except OSError:
            pass  # файл графа уже убран — мусор в папке вывода не оставляем
    return out


__all__ = ["mix", "mix_command", "build_graph", "censored_pieces", "audio_tmp",
           "SAMPLE_RATE", "CHANNELS", "VOICE_SILENT_DB", "CENSOR_RAMP_S",
           "CENSOR_RAMP_STEPS", "TMP_PREFIX"]


def audio_tmp(out: str) -> str:
    """Имя временного аудиофайла рядом с итоговым (его убирает вызывающий)."""
    return os.path.join(os.path.dirname(os.path.abspath(out)), TMP_PREFIX + "audio.m4a")
