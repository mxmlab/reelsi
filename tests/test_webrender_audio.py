# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Звук рендера без After Effects: микс ffmpeg по плану сцены.

Проверяется то, ради чего микс и написан: звук ролика собирается РОВНО из плана —
оттуда же, откуда его берёт `.jsx`, поэтому разойтись с AE микс может только там, где
молчит сам план.

1. **Граф читается как план.** В теле фильтра видны громкость голоса (`voice_db`),
   громкость музыки (`music_db`), куски голоса (их задержка в ролике, вход в исходник
   и длина — из `plan.audio.segments`) и окна цензуры: рампа 0.02 с ступенями по
   `CENSOR_RAMP_STEPS`, полка окна — дно `VOICE_SILENT_DB` (как `censorLayer` в `.jsx`).
2. **Мутация ловится.** Убрать `voice_db` из кусков или цензуру из плана — тест краснеет.
3. **Звук действительно сводится.** Настоящий ffmpeg (если он в PATH): в окне цензуры
   голос падает, вне окна возвращается, а смена `voice_db` на 6 дБ опускает замер ровно
   на эти 6 дБ. Так проверяется не текст графа, а результат.
4. **Куски голоса — из плана.** `scene_plan` кладёт в `plan.audio.segments` те же куски
   камеры 1, что видны в EDL предпросмотра (`virtual_edl.audio`), и склеивает соседние,
   стыкующиеся вплотную и в монтаже, и в исходнике.
5. **Пустой план — пустой граф.** Ни голоса, ни музыки, ни звуков: микс не вызывается
   вовсе, и рендер отдаёт видео как есть (а не тишину с лишней дорожкой).

Запуск: python -m pytest tests/test_webrender_audio.py -q
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import webrender_audio as wa  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="живой микс требует ffmpeg")
ffprobe = pytest.mark.skipif(not shutil.which("ffprobe"), reason="замер требует ffprobe")


@pytest.fixture()
def xml_subs(tmp_path):
    """Тот же клип с субтитрами, что у соседних тестов плана."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _plan(tmp_path, **audio):
    """План сцены с нужным звуком: файлы-пустышки (граф их не читает), план — читает."""
    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.wav"
    voice.write_bytes(b"RIFF")
    music.write_bytes(b"RIFF")
    a = {"voice_src": str(voice), "voice_db": -3.0, "music_path": str(music),
         "music_db": -18.0, "censor": [[2.0, 2.4]],
         "segments": [{"ts": 0.0, "te": 2.0, "src": 0.0},
                      {"ts": 2.0, "te": 4.0, "src": 10.0},
                      {"ts": 5.0, "te": 6.0, "src": 30.0}],
         "sfx": []}
    a.update(audio)
    return {"dur": 6.0, "audio_fade": 0.010, "audio": a}


def _capture(plan, video, out, **kw):
    """Собрать граф, не запуская ffmpeg: тело фильтра + команда микса.

    `run` — дверь микса: пишем то, что он передал бы ffmpeg (файл графа и команду), и
    «создаём» выходной файл, иначе микс справедливо ругается на пустой результат.
    """
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        i = cmd.index("-filter_complex_script")
        with open(cmd[i + 1], encoding="utf-8") as f:
            seen["graph"] = f.read()
        open(out, "wb").write(b"mp4")
        return type("R", (), {"returncode": 0, "stderr": b""})()

    res = wa.mix(plan, video, out, run=run, **kw)
    return res, seen


# --------------------------------------------------------------------------- #
# 1. Граф = план
# --------------------------------------------------------------------------- #
def test_graph_carries_plan_levels_and_voice_pieces(tmp_path):
    """Громкости и куски голоса — из плана, а не из своих чисел."""
    plan = _plan(tmp_path)
    graph, inputs = wa.build_graph(plan)
    assert inputs[0] == plan["audio"]["voice_src"], "первый вход — голос (его читает [0:a])"
    assert inputs[1] == plan["audio"]["music_path"], inputs
    # Громкости — ровно те, что в плане (dB, а не пересчитанные «на глаз»).
    assert "volume=-18dB" in graph, graph          # музыка
    assert "volume=-3dB" in graph, graph           # голос: куски вне окна цензуры
    # Куски голоса: задержка в ролике, вход в исходник, длина. Куски плана режутся ещё и
    # окном цензуры (2.0–2.4): первый кусок заканчивается за 0.02 с до окна — там
    # начинается рампа.
    assert "atrim=start=%s:duration=%s" % (wa._t(0), wa._t(2.0 - wa.CENSOR_RAMP_S)) in graph, graph
    assert "adelay=delays=2000:all=1" in graph, graph     # второй кусок — с 2 с
    assert "adelay=delays=5000:all=1" in graph, graph     # третий — с 5 с
    assert "atrim=start=%s:duration=%s" % (wa._t(30), wa._t(1)) in graph, graph
    # Микро-фейд краёв — тот же AUDIO_FADE, что у шаблона.
    assert "afade=t=in:st=0:d=0.01:curve=tri" in graph, graph
    # Длина микса — окно рендера.
    assert "atrim=start=0:end=6" in graph, graph


def test_graph_censors_the_voice_window(tmp_path):
    """Цензура: окно 2.0–2.4 гасит голос до −100 dB ступенями рампы по 0.02 с."""
    plan = _plan(tmp_path)
    graph, _ = wa.build_graph(plan)
    # Полка окна: кусок с дном цензуры и громкостью −100 dB.
    assert "volume=%sdB" % wa._fmt(wa.VOICE_SILENT_DB) in graph, graph
    # Рампа: 0.02 с делится на CENSOR_RAMP_STEPS ступеней — значит в графе есть
    # кусочки длиной ramp/steps, а не только целые куски плана.
    step = wa.CENSOR_RAMP_S / wa.CENSOR_RAMP_STEPS
    assert "duration=%s" % wa._t(step) in graph, ("нет ступеней рампы", graph)
    # Ступени — с громкостью МЕЖДУ голосом и дном (это и есть рампа).
    gains = [float(m) for m in re.findall(r"volume=(-?[\d.]+)dB", graph)]
    assert any(wa.VOICE_SILENT_DB < g < -3.0 - 1 for g in gains), \
        ("нет промежуточных ступеней рампы", sorted(set(gains)))
    assert min(gains) == pytest.approx(wa.VOICE_SILENT_DB), sorted(set(gains))
    assert max(gains) == pytest.approx(-3.0), sorted(set(gains))


def test_censored_pieces_cover_the_window_exactly(tmp_path):
    """Куски цензуры покрывают окно без дыр и наложений: `amix` не сложил бы голос сам с собой."""
    plan = _plan(tmp_path)
    t0, t1 = 0.0, 6.0
    pieces = wa.censored_pieces(plan["audio"]["segments"], plan["audio"]["censor"],
                                plan["audio"]["voice_db"], t0, t1)
    assert pieces, "куски голоса не собрались"
    for a, b in zip(pieces, pieces[1:]):
        end = a[0] + a[2]
        assert end <= b[0] + 1e-9, (a, b)
    # Внутри окна цензуры громкость — дно, вне — голос.
    inside = [g for (d, _s, ln, g) in pieces if 2.1 <= d + ln / 2 <= 2.3]
    assert inside and all(g == pytest.approx(wa.VOICE_SILENT_DB) for g in inside), inside


def test_render_window_shifts_music_and_trims_a_running_sound(tmp_path):
    """Окно рендера сдвигает ВСЁ: музыка играет со своей секунды, звук — со своей середины.

    В AE музыка лежит от нуля композиции, а слой звука, начавшийся до окна, попадает в
    кадр уже идущим. Рендер куска ролика обязан звучать так же, иначе звук в проверочном
    куске окажется сдвинут на столько, на сколько окно отстоит от начала ролика.
    """
    pop = tmp_path / "pop.wav"
    pop.write_bytes(b"RIFF")
    plan = _plan(tmp_path, sfx=[
        {"kind": "pop", "media": str(pop), "db": -8.0, "base": -8.0,
         "events": [{"t": 2.5, "in": 0.0, "out": 1.0}]}])   # начался ДО окна 3…4 с
    graph, _ = wa.build_graph(plan, start=3.0, dur=1.0)
    # Музыка — с 3-й секунды файла (в AE она идёт от нуля композиции).
    assert "atrim=start=%s,asetpts=N/SR/TB,volume=-18dB" % wa._t(3.0) in graph, graph
    # Звук: полсекунды уже прошло — играет с 0.5 с файла, длиной 0.5 с, без задержки.
    assert "atrim=start=%s:duration=%s" % (wa._t(0.5), wa._t(0.5)) in graph, graph
    assert "adelay=delays=0:all=1" not in graph, graph


def test_whole_clip_window_leaves_the_music_at_zero(tmp_path):
    """Целый ролик (окно с нуля) — сдвига музыки в графе нет: он нулевой."""
    plan = _plan(tmp_path)
    graph, _ = wa.build_graph(plan)
    assert "atrim=start=%s,asetpts=N/SR/TB,volume=-18dB" % wa._t(0.0) not in graph, graph
    assert "volume=-18dB" in graph, graph


def test_music_only_plan_needs_no_voice_file(tmp_path):
    """План без кусков голоса (все клипы камеры 1 выключены): микс — музыка, без голоса.

    Файла голоса в таком плане нет вовсе, и «нет файла» тут — не поломка входа: голос
    в ролике не звучит. Раньше пустой вход голоса ронял микс музыки.
    """
    music = tmp_path / "music.wav"
    music.write_bytes(b"RIFF")
    plan = {"dur": 5.0, "audio_fade": 0.010,
            "audio": {"voice_src": str(tmp_path / "нет-голоса.wav"), "voice_db": -2.0,
                      "music_path": str(music), "music_db": -14.0, "censor": [],
                      "segments": [], "sfx": []}}
    graph, inputs = wa.build_graph(plan)
    assert inputs == [str(music)], inputs          # голос в графе не упомянут вовсе
    assert "asplit" not in graph, graph            # и кусков голоса (их режет asplit) нет
    assert "volume=-14dB" in graph, graph
    res, seen = _capture(plan, str(tmp_path / "v.mp4"), str(tmp_path / "o.mp4"))
    assert res == str(tmp_path / "o.mp4"), res
    assert seen["cmd"][seen["cmd"].index("-map") + 1] == "1:v:0", seen["cmd"]


def test_render_window_trims_the_graph(tmp_path):
    """Рендер куска ролика: граф режет и куски голоса, и длину микса по окну."""
    plan = _plan(tmp_path)
    graph, _ = wa.build_graph(plan, start=2.0, dur=2.0)
    # Кусок 0–2 с кончился на начале окна (в кадр не попал), кусок 2–4 с идёт в окне с
    # нуля (в исходнике — с 10-й секунды); кусок 5–6 с — за окном.
    assert "atrim=start=%s" % wa._t(10) in graph, graph
    assert "atrim=start=%s" % wa._t(30) not in graph, "кусок за окном остался в графе"
    assert "atrim=start=0:end=2" in graph, graph


def test_sfx_events_go_to_the_mix_with_plan_numbers(tmp_path):
    """Звуки: файл, старт (`t` из плана), обрезка `in/out` и громкость `base+db`."""
    pop = tmp_path / "pop.wav"
    pop.write_bytes(b"RIFF")
    plan = _plan(tmp_path, sfx=[
        {"kind": "pop", "media": str(pop),
         "events": [{"t": 1.0, "in": 0.0, "out": 0.1}], "db": -8.0, "base": -8.0},
        # Видеопереход в миксе не звучит: в AE это видеофайл перехода, а кадры в
        # рендере без AE склеены встык (см. webrender_audio._sfx_events).
        {"kind": "transition", "media": str(tmp_path / "trans.mov"),
         "events": [{"t": 3.0, "in": 0.0, "out": 0.4}], "db": 0.0, "base": 0.0}])
    graph, inputs = wa.build_graph(plan)
    assert str(pop) in inputs, inputs
    assert "trans.mov" not in graph, "звук видеоперехода не должен попадать в микс"
    gain = wa._fmt(wa._db2lin(-16.0))
    assert "volume=%s" % gain in graph, (gain, graph)
    assert "atrim=start=%s:duration=%s" % (wa._t(0), wa._t(0.1)) in graph, graph
    assert "adelay=delays=1000:all=1" in graph, graph


def test_sfx_without_out_plays_to_the_end_of_the_file(tmp_path):
    """Событие без `out` в плане — звучит до конца файла, как слой .jsx без outPoint.

    Так ведут себя ризер и whoosh. Обрезки по времени у такого события в графе нет:
    длину микса держит тишина-основа, а конец — сам файл.
    """
    riser = tmp_path / "riser.wav"
    riser.write_bytes(b"RIFF")
    plan = _plan(tmp_path, sfx=[
        {"kind": "riser", "media": str(riser), "db": 0.0, "base": 0.0,
         "events": [{"t": 0.5, "in": 0.25, "out": None}]}])
    graph, inputs = wa.build_graph(plan)
    idx = inputs.index(str(riser))
    # Цепочка события: `atrim=start=<in>` БЕЗ `duration` — конец задаёт сам файл.
    assert "[%d:a]atrim=start=%s,asetpts=N/SR/TB,aresample" % (idx, wa._t(0.25)) in graph, graph
    assert "adelay=delays=500:all=1" in graph, graph     # старт — из плана (0.5 с)


def test_graph_is_empty_when_the_plan_has_no_audio(tmp_path):
    """Ни голоса, ни музыки, ни звуков — графа нет: видео остаётся как есть."""
    graph, inputs = wa.build_graph({"dur": 3.0, "audio": {}})
    assert graph == "" and inputs == [], (graph, inputs)


def test_mix_reports_a_missing_audio_file(tmp_path):
    """Файла из плана нет — это поломка входа, а не «тишина»: микс не собирается."""
    plan = _plan(tmp_path)
    plan["audio"]["voice_src"] = str(tmp_path / "нет-такого.wav")
    with pytest.raises(ReelsiError) as exc:
        wa.mix(plan, str(tmp_path / "v.mp4"), str(tmp_path / "o.mp4"),
               run=lambda *a, **k: None)
    # Текст ошибки — шаблон с переменной (её переводит словарь): сверяем имя файла
    # без регистра — Windows отдаёт `.WAV` там, где в плане `.wav`.
    assert "нет-такого" in str(exc.value.vars.get("path", "")).lower(), exc.value.vars
    assert exc.value.code == "webrender_audio_missing", exc.value.code


def test_mix_leaves_no_graph_file_behind(tmp_path):
    """Файл графа — служебный: после микса в папке вывода его нет."""
    plan = _plan(tmp_path)
    out = tmp_path / "o.mp4"
    _capture(plan, str(tmp_path / "v.mp4"), str(out))
    assert not [p for p in os.listdir(tmp_path) if p.startswith(wa.TMP_PREFIX)], \
        os.listdir(tmp_path)


def test_mutation_without_voice_db_is_caught(tmp_path, monkeypatch):
    """Мутация: не применять `voice_db` — тест на громкость голоса обязан покраснеть."""
    plan = _plan(tmp_path)
    graph, _ = wa.build_graph(plan)
    assert "volume=-3dB" in graph, graph           # так граф выглядит сегодня
    monkeypatch.setattr(wa, "_num", lambda v, default=0.0: (
        0.0 if v == plan["audio"]["voice_db"] else default))
    bad, _ = wa.build_graph(plan)
    assert "volume=-3dB" not in bad, "мутация не сработала — тест ничего не проверяет"
    with pytest.raises(AssertionError):
        assert "volume=-3dB" in bad


def test_mutation_without_censor_is_caught(tmp_path):
    """Мутация: выкинуть цензуру из плана — окна обязаны исчезнуть из графа."""
    plan = _plan(tmp_path)
    plan2 = json.loads(json.dumps(plan))
    plan2["audio"]["censor"] = []
    graph, _ = wa.build_graph(plan2)
    assert "volume=%sdB" % wa._fmt(wa.VOICE_SILENT_DB) not in graph, \
        "полка цензуры осталась при пустом списке окон"
    assert "volume=-3dB" in graph, "громкость голоса пропала вместе с цензурой"


# --------------------------------------------------------------------------- #
# 2. Живой микс: замер громкости
# --------------------------------------------------------------------------- #
def _ffmpeg(*args):
    p = subprocess.run(["ffmpeg", *args], capture_output=True, text=True, errors="replace")
    assert p.returncode == 0, (args, p.stderr[-1500:])
    return p.stdout


def _rms_at(path, a, b):
    """Средний RMS (dB) в окне времени [a, b] — по кадрам `astats`.

    Тишину НЕ отбрасываем (цензура уводит голос в −100 dB), а кадры без звука вовсе
    (`-inf`) пропускаем: их даёт цифровая тишина, которой в миксе нет.
    """
    out = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", path, "-af",
         "astats=metadata=1:reset=1,"
         "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
         "-f", "null", "-"], capture_output=True, text=True, errors="replace").stdout
    vals, cur = [], None
    for line in out.splitlines():
        if "pts_time" in line:
            cur = float(line.split("pts_time:")[1])
        elif "RMS_level" in line and cur is not None and a <= cur <= b:
            try:
                vals.append(float(line.split("=")[1]))
            except ValueError:
                pass                      # `-inf`: цифровая тишина, в миксе её нет
    assert vals, f"нет кадров в окне {a}–{b} с: файл {path}"
    return sum(vals) / len(vals)


@ffmpeg
@ffprobe
def test_live_mix_keeps_levels_windows_and_the_picture(tmp_path):
    """Живой ffmpeg: голос = voice_db, в окне цензуры тишина, картинка не перекодирована."""
    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.wav"
    pic = tmp_path / "pic.mp4"
    out = tmp_path / "out.mp4"
    _ffmpeg("-y", "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=40:sample_rate=48000", "-ac", "2", str(voice))
    _ffmpeg("-y", "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=880:duration=20:sample_rate=48000", "-ac", "2", str(music))
    _ffmpeg("-y", "-v", "error", "-f", "lavfi", "-i",
            "color=c=black:s=320x240:d=8:r=30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(pic))
    plan = {"dur": 8.0, "audio_fade": 0.010,
            "audio": {"voice_src": str(voice), "voice_db": 0.0, "music_path": "",
                      "music_db": -20.0, "censor": [[2.0, 2.4]],
                      "segments": [{"ts": 0.0, "te": 4.0, "src": 0.0},
                                   {"ts": 4.0, "te": 8.0, "src": 10.0}],
                      "sfx": []}}
    wa.mix(plan, str(pic), str(out), emit=lambda *a, **k: None)

    # Видео — БЕЗ перекодирования: тот же кодек и то же число кадров, что у картинки.
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
        capture_output=True, text=True, errors="replace").stdout)
    streams = {s["codec_type"]: s for s in probe["streams"]}
    assert streams["video"]["codec_name"] == "h264", streams["video"]
    assert int(streams["video"]["nb_frames"]) == 240, streams["video"]
    assert streams["audio"]["codec_name"] == "aac", streams["audio"]
    assert int(streams["audio"]["sample_rate"]) == wa.SAMPLE_RATE, streams["audio"]
    # Длительности видео и звука совпадают (кадр — 1/30 с, допуск кадр).
    assert abs(float(streams["video"]["duration"]) - float(streams["audio"]["duration"])) \
        <= 1 / 30.0, streams

    plain = _rms_at(str(out), 0.3, 1.9)
    inside = _rms_at(str(out), 2.05, 2.35)
    after = _rms_at(str(out), 2.5, 3.9)
    assert inside < plain - 40, f"цензура не сработала: {plain} -> {inside}"
    assert abs(after - plain) < 0.5, f"после окна громкость не вернулась: {after} vs {plain}"

    # Смена voice_db на 6 дБ опускает замер ровно на эти 6 дБ. Допуск 0.05 дБ — на
    # порядок меньше шага и при этом ловит «громкость не применилась» и «применилась
    # дважды»: на живом прогоне разница 5.994 дБ (округление AAC на 16 бит).
    plan6 = json.loads(json.dumps(plan))
    plan6["audio"]["voice_db"] = -6.0
    out6 = tmp_path / "out6.mp4"
    wa.mix(plan6, str(pic), str(out6), emit=lambda *a, **k: None)
    plain6 = _rms_at(str(out6), 0.3, 1.9)
    assert abs((plain - plain6) - 6.0) < 0.05, (plain, plain6)

    # Цензура в миксе с музыкой: голос уходит, музыка остаётся (в AE музыка не ныряет).
    plan_m = json.loads(json.dumps(plan))
    plan_m["audio"]["music_path"] = str(music)
    out_m = tmp_path / "outm.mp4"
    wa.mix(plan_m, str(pic), str(out_m), emit=lambda *a, **k: None)
    assert _rms_at(str(out_m), 2.05, 2.35) > -60, "музыка ушла вместе с цензурой голоса"


# --------------------------------------------------------------------------- #
# 3. Куски голоса — из плана сцены
# --------------------------------------------------------------------------- #
def test_voice_segments_merge_adjacent_and_convert_frames_to_seconds():
    """Кадры → секунды и склейка стыкующихся кусков: 60 к/с, два клипа подряд."""
    from core.xml2ae.plan_audio import voice_segments
    clips = [[0, 60, 120, 180, True, 50.4], [60, 120, 180, 240, True, 50.4]]
    segs = voice_segments(clips, 60.0)
    assert len(segs) == 1, segs                       # подряд и в монтаже, и в исходнике
    assert segs[0] == {"ts": 0.0, "te": 2.0, "src": 2.0}, segs
    # Выключенный клип звука не даёт, а разрыв в исходнике склейке не подлежит.
    clips2 = [[0, 60, 120, 180, True, 50.4], [60, 120, 300, 360, True, 50.4],
              [120, 180, 400, 460, False, 50.4]]
    segs2 = voice_segments(clips2, 60.0)
    assert [s["src"] for s in segs2] == [2.0, 5.0], segs2


def test_plan_audio_segments_match_the_preview_edl(xml_subs):
    """`plan.audio.segments` — те же куски камеры 1, что играет предпросмотр (EDL)."""
    from core import xml2ae
    plan = xml2ae.scene_plan(xml_subs, emit=lambda *a, **k: None)
    edl = xml2ae.virtual_edl(xml_subs)
    segs, audio = plan["audio"]["segments"], edl["audio"]
    assert segs, "план не несёт кусков голоса — микс резать нечем"
    assert len(segs) >= len(audio) - 2, (len(segs), len(audio))
    fps = float(plan["fps"])
    for got, want in zip(segs, audio):
        assert abs(got["ts"] - want["ts"]) <= 1.0 / fps, (got, want)
        assert abs(got["te"] - want["te"]) <= 1.0 / fps, (got, want)
        assert abs(got["src"] - want["src"]) <= 1.0 / fps, (got, want)
    # Куски идут по времени и не налезают друг на друга: иначе amix сложил бы голос сам
    # с собой (громче задуманного).
    for a, b in zip(segs, segs[1:]):
        assert a["ts"] < a["te"] <= b["ts"] + 1e-6, (a, b)


def test_plan_audio_segments_reach_the_graph(xml_subs):
    """Сквозная сверка: план сцены клипа → граф микса (куски, цензура, громкость)."""
    from core import xml2ae
    plan = xml2ae.scene_plan(xml_subs, style={"voice_db": -2.5}, emit=lambda *a, **k: None)
    graph, inputs = wa.build_graph(plan)
    assert inputs[0] == plan["audio"]["voice_src"], inputs
    first = plan["audio"]["segments"][0]
    assert "atrim=start=%s" % wa._t(first["src"]) in graph, graph
    assert "volume=-2.5dB" in graph, graph
    # У клипа-фикстуры есть слова со звёздочкой — значит окна цензуры в графе видны.
    if plan["audio"]["censor"]:
        assert "volume=%sdB" % wa._fmt(wa.VOICE_SILENT_DB) in graph, graph


def test_scene_route_carries_audio_segments(xml_subs):
    """`/api/scene` отдаёт куски голоса странице рендера: их читает и микс, и превью."""
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        d = c.post("/api/scene", json={"xml": xml_subs}).get_json()
    assert d.get("ok"), d
    segs = (d["plan"].get("audio") or {}).get("segments")
    assert isinstance(segs, list) and segs, d["plan"].get("audio")


def test_graph_has_no_leftover_percent_tokens(tmp_path):
    """В теле фильтра нет незамещённых `%`-токенов: ffmpeg упал бы на всём графе."""
    graph, _ = wa.build_graph(_plan(tmp_path))
    assert not re.search(r"%[sdfg(]", graph), graph
