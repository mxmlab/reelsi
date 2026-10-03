# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание OU: обработанный голос в нарезке (voice_fx.cut) и в итоговом треке AE (final).

Живые deep-filter, ffmpeg, VST и After Effects здесь НЕ запускаются: подменяются
`render_cached`, `subprocess.run` и `final_voice_for_build`. Проверяются контракты:

* `apply_cut_fx` — файл нарезки подменяется ПОСЛЕ синхронизации камер (смещения
  считаются по сырому звуку), с громкостью стиля спикера, а сбой шумодава оставляет
  нарезку на сыром звуке;
* `analysis_wav` — тот же ffmpeg, что у `sync.extract_audio` (моно, `sync.SR`, PCM),
  и запись атомарная: сбой не оставляет нарезку без звука вовсе;
* `ensure_final_voice` / `final_voice_for_build` — `<стем>.voice.wav` рядом с XML,
  повторный вызов не копирует, смена настроек копирует заново, нет спикера или
  флага — None, старый файл не удаляется;
* сборка .jsx — VOICE_WAV с путём, звук видео камеры 1 выключен, громкость/фейды/
  цензура на аудиослое; без обработанного голоса .jsx остаётся прежним байт в байт
  (эталон tests/fixtures/golden_geometry.jsx).

Запуск:  python -m pytest tests/test_voicefx_wiring.py -q
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
sys.path.insert(0, HERE)

from core import omni_cut, speakers, styles, sync, voicefx  # noqa: E402
from core.project_file import write_project  # noqa: E402
from core.umsg import ReelsiError, umsg  # noqa: E402
from core.xml2ae import build as xml2ae_build  # noqa: E402
from core.xml2ae.jsutil import _js  # noqa: E402
from tests.test_geometry_python import _build as _golden_build  # noqa: E402
from tests.test_geometry_python import _mask_assets  # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "golden_geometry.jsx")
STYLE_DB = -6.5                      # громкость голоса стиля спикера из фикстуры


def _noop(*a, **k):
    """emit-заглушка: строки лога в тестах не нужны."""
    return None


def _raise(exc):
    """Заглушка, которая падает: проверяем, что до неё не дошло (или что сбой прожит)."""
    def _boom(*a, **k):
        raise exc
    return _boom


# --------------------------------------------------------------------------- #
# Изоляция: кеш, профили спикеров, стили — в tmp_path (личные файлы не трогаем)
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def voicefx_dir(tmp_path, monkeypatch):
    """Запечённые треки и превью — во временный каталог, не в боевую `_voicefx/`."""
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))


@pytest.fixture(autouse=True)
def _isolate_censor(monkeypatch):
    """Сборка детерминирована: цензура читает поставочные списки, а не личные файлы."""
    from core import censor
    monkeypatch.setattr(censor, "USER_PATHS", {"bad": "", "ok": ""})
    monkeypatch.setattr(censor, "_cache", {"bad": (None, None, censor.DEFAULT_BAD),
                                           "ok": (None, None, censor.DEFAULT_OK)})


@pytest.fixture()
def speakers_dir(tmp_path, monkeypatch):
    """Профили спикеров — в tmp_path: в репозитории личные профили пользователя."""
    d = tmp_path / "speakers"
    d.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    return d


@pytest.fixture()
def style_db(tmp_path, monkeypatch):
    """Стиль AE с громкостью голоса: стиль спикера — обычный пресет `styles/*.json`."""
    d = tmp_path / "styles"
    d.mkdir()
    (d / "Тест.json").write_text(json.dumps({"voice_db": STYLE_DB}), encoding="utf-8")
    monkeypatch.setattr(styles, "STYLE_DIR", str(d))
    return STYLE_DB


@pytest.fixture()
def cam1(tmp_path):
    """Файл камеры 1: содержимое не читается (ffmpeg подменён) — важно наличие."""
    p = tmp_path / "cam1.mp4"
    p.write_bytes(b"fake camera file")
    return str(p)


@pytest.fixture()
def xml_path(tmp_path):
    """XML нарезки: рядом с ним живут сайдкары `.project.json` и `.voice.*`."""
    p = tmp_path / "clip.xml"
    p.write_text("<x/>", encoding="utf-8")
    return str(p)


@pytest.fixture()
def xml_subs(tmp_path):
    """Фикстура нарезки (2 камеры, вторая включается на 8-й секунде) — как в golden-тестах."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _speaker(speakers_dir, key="Голос", **fx):
    """Профиль спикера с обработкой голоса; возвращает его же словарём."""
    speakers.save(key, {"label": key, "style": "Тест", "voice_fx": fx})
    return speakers.load(key) or {}


# --------------------------------------------------------------------------- #
# 1. Нарезка: apply_cut_fx — подмена файла нарезки
# --------------------------------------------------------------------------- #
def test_apply_cut_fx_swaps_wav_with_style_gain(tmp_path, speakers_dir, style_db, cam1,
                                                monkeypatch):
    """Обработка включена — wavs[0] перезаписан обработанным голосом и громкостью стиля.

    Отдельной галки «для нарезки» больше нет: включённая обработка означает, что
    нарезка слушает тот же голос, что уедет в ролик. Профиль здесь ещё и СТАРОГО
    вида (`cut`/`final=false`) — миграция при чтении обязана его не выключить.

    Плагинов в цепочке нет (в профиле пусто), поэтому цепочка — это дорожка
    шумодава: плагины, когда они есть, печёт поверх неё та же дверь вывода
    (`render_cached`), см. `apply_cut_fx` и tests/test_voice_cut_chain.py.
    """
    prof = _speaker(speakers_dir, denoise={"on": True, "engine": "deepfilter",
                                           "atten_db": 40}, cut=False, final=False)
    wav0 = tmp_path / "a0.wav"
    wav0.write_bytes(b"raw camera audio")
    seen = {}

    def fake_denoise_track(src, dn, start=0.0, dur=None, emit=_noop,
                           cancelled=None, progress=None, pid_of=None):
        seen["denoise"] = (src, dn)
        return "/cache/denoise.wav"

    def fake_analysis(full, dst, gain_db=0.0, emit=_noop):
        seen["analysis"] = (full, dst, gain_db)
        return dst

    monkeypatch.setattr(voicefx, "denoise_track", fake_denoise_track)
    monkeypatch.setattr(voicefx, "analysis_wav", fake_analysis)
    lines = []
    assert voicefx.apply_cut_fx(str(wav0), cam1, prof,
                                emit=lambda line="", **kw: lines.append(line)) is True

    assert seen["denoise"][0] == cam1, "обрабатывается не звук камеры 1"
    assert seen["denoise"][1] == {"on": True, "engine": "deepfilter",
                                  "atten_db": 40, "mix": 100}
    # Меняется ровно файл нарезки — и с громкостью стиля спикера, а не с нулём
    assert seen["analysis"] == ("/cache/denoise.wav", str(wav0), style_db)
    assert any("обработка для нарезки" in ln for ln in lines), "нет строки о начале обработки"


def test_apply_cut_fx_ignores_only_empty_chain(speakers_dir, cam1, tmp_path, monkeypatch):
    """Пустая обработка — не работа; старые снятые галки обработку НЕ выключают.

    Профили владельца записаны с `cut=false`: если читать их как решение, нарезка
    молча шла бы по сырому звуку при включённом шумодаве.
    """
    wav0 = str(tmp_path / "a0.wav")
    monkeypatch.setattr(voicefx, "denoise_track", _raise(AssertionError("рендер без обработки")))
    monkeypatch.setattr(voicefx, "analysis_wav", _raise(AssertionError("подмена без обработки")))

    prof = _speaker(speakers_dir, denoise={"on": True, "atten_db": 40}, cut=False, final=False)
    assert voicefx.voice_fx_on(voicefx.normalize_fx(prof["voice_fx"])) is True, \
        "старый профиль с cut/final=false выключил обработку"

    prof = _speaker(speakers_dir, key="Пустой", cut=True,
                    vst=[{"path": "C:/p.vst3", "on": False}])
    assert voicefx.apply_cut_fx(wav0, cam1, prof, emit=_noop) is False, \
        "обработка пуста: ни шумодава, ни включённых плагинов"

    assert voicefx.apply_cut_fx(wav0, cam1, None, emit=_noop) is False, "профиля нет вовсе"


def test_apply_cut_fx_failure_keeps_raw_audio(speakers_dir, cam1, tmp_path, monkeypatch):
    """Шумодав упал: предупреждение в лог, wavs[0] не тронут — режем по сырому звуку."""
    prof = _speaker(speakers_dir, denoise={"on": True, "atten_db": 40}, cut=True)
    wav0 = tmp_path / "a0.wav"
    wav0.write_bytes(b"raw camera audio")
    monkeypatch.setattr(voicefx, "denoise_track",
                        _raise(ReelsiError(umsg("deepfilter_missing", "нет шумодава"))))
    monkeypatch.setattr(voicefx, "analysis_wav", _raise(AssertionError("подменили после сбоя")))
    lines = []

    assert voicefx.apply_cut_fx(str(wav0), cam1, prof,
                                emit=lambda line="", **kw: lines.append(line)) is False
    assert wav0.read_bytes() == b"raw camera audio", "файл нарезки испорчен сбоем обработки"
    assert any("не обработан" in ln for ln in lines), "сбой обработки не виден в логе"


def test_analysis_wav_command_is_like_sync_extract(tmp_path, monkeypatch):
    """ffmpeg тот же, что у sync.extract_audio: моно, sync.SR, PCM, плюс volume стиля."""
    calls = []
    dst = tmp_path / "a0.wav"
    dst.write_bytes(b"raw")

    def fake_run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        calls.append(cmd)
        with open(cmd[cmd.index("-c:a") + 2], "wb") as f:
            f.write(b"RIFF processed voice")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(voicefx.subprocess, "run", fake_run)
    out = voicefx.analysis_wav("full.wav", str(dst), gain_db=STYLE_DB, emit=_noop)

    cmd = calls[0]
    assert out == str(dst) and dst.read_bytes() == b"RIFF processed voice"
    assert cmd[cmd.index("-ac") + 1] == "1", "нарезка слушает моно"
    assert cmd[cmd.index("-ar") + 1] == str(sync.SR), "частота не та, что у sync.extract_audio"
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le"
    assert cmd[cmd.index("-af") + 1] == "volume=%gdB" % STYLE_DB
    assert cmd[-2:] == ["-loglevel", "error"]
    # Пишем во временный файл и заменяем: сбой ffmpeg не должен оставить нарезку без звука
    assert cmd[cmd.index("-c:a") + 2] != str(dst)


def test_analysis_wav_failure_keeps_old_file(tmp_path, monkeypatch):
    """ffmpeg упал: старый файл нарезки цел, временный убран."""
    dst = tmp_path / "a0.wav"
    dst.write_bytes(b"raw camera audio")

    def fake_run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        with open(cmd[cmd.index("-c:a") + 2], "wb") as f:   # ffmpeg успел создать огрызок
            f.write(b"")
        return subprocess.CompletedProcess(cmd, 1, "", "Invalid data found\n")

    monkeypatch.setattr(voicefx.subprocess, "run", fake_run)
    with pytest.raises(ReelsiError):
        voicefx.analysis_wav("full.wav", str(dst), gain_db=0.0, emit=_noop)

    assert dst.read_bytes() == b"raw camera audio"
    assert [p.name for p in tmp_path.iterdir()] == ["a0.wav"], "временный файл остался"


def test_omni_cut_applies_voice_after_camera_sync():
    """omni_cut зовёт обработку ПОСЛЕ find_offset и только на пути gigaam.

    Синхронизация камер обязана считаться по СЫРОМУ звуку: подмена wavs[0] раньше
    смещений уехала бы вместе с ними. Проверяется текстом модуля — прогон нарезки
    целиком тянет ASR, LLM и рендер черновика.
    """
    src = open(omni_cut.__file__, encoding="utf-8").read()
    gigaam = src.index("if gigaam_path:")
    offset = src.index("sync.find_offset")
    cut = src.index("voicefx.apply_cut_fx")
    assert offset < gigaam < cut, "обработка голоса встала раньше синхронизации камер"
    assert src.count("voicefx.apply_cut_fx") == 1, "второй вызов подмены файла нарезки"
    # Плагины печёт только вывод: нарезка итоговый голос не запекает.
    assert "ensure_final_voice" not in src, "нарезка печёт итоговый голос с плагинами"


# --------------------------------------------------------------------------- #
# 2. Итоговый голос: ensure_final_voice и final_voice_for_build
# --------------------------------------------------------------------------- #
def test_ensure_final_voice_bakes_once_then_only_on_change(xml_path, cam1, tmp_path,
                                                           monkeypatch):
    """Первый вызов копирует трек и пишет ключ; тот же ключ — не копируем; смена — копируем."""
    rendered = tmp_path / "cache.wav"
    rendered.write_bytes(b"RIFF processed voice")
    calls = {"render": 0, "copy": 0}

    def fake_render(src, fx, emit=_noop, progress=None, cancelled=None, pid_of=None):
        calls["render"] += 1
        return str(rendered)

    def fake_copy(src, dst):
        calls["copy"] += 1
        shutil.copyfile(src, dst)

    monkeypatch.setattr(voicefx, "render_cached", fake_render)
    monkeypatch.setattr(voicefx, "_copy_atomic", fake_copy)
    fx = {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40}}

    got = voicefx.ensure_final_voice(xml_path, cam1, fx, emit=_noop)[0]
    assert got == voicefx.final_voice_path(xml_path)
    assert got == os.path.splitext(xml_path)[0] + ".voice.wav", "файл не рядом с XML"
    assert open(got, "rb").read() == b"RIFF processed voice"
    meta = json.loads(open(os.path.splitext(xml_path)[0] + ".voice.json",
                           encoding="utf-8").read())
    assert meta["key"] and meta["src"] == cam1
    assert calls == {"render": 1, "copy": 1}

    # Тот же ключ кеша — файл уже запечён: ни рендера, ни копии
    assert voicefx.ensure_final_voice(xml_path, cam1, fx, emit=_noop)[0] == got
    assert calls == {"render": 1, "copy": 1}

    # Сменили силу шумодава — ключ другой, копируем заново
    voicefx.ensure_final_voice(xml_path, cam1,
                               {"denoise": {"on": True, "engine": "deepfilter",
                                            "atten_db": 60}}, emit=_noop)
    assert calls == {"render": 2, "copy": 2}


def _write_sidecar(xml_path, cam1, speaker=None):
    """Сайдкар нарезки рядом с XML — так его пишет нарезка (core.project_file)."""
    proj = {"cams": [cam1], "offsets": [0.0], "fps": 60, "cam_return": 2, "scale": 50.4,
            "keep": [[0.0, 1.0]]}
    if speaker:
        proj["speaker"] = speaker
    write_project(os.path.splitext(xml_path)[0] + ".project.json", proj)


def test_final_voice_for_build_needs_sidecar_speaker_and_processing(xml_path, cam1,
                                                                    speakers_dir, tmp_path,
                                                                    monkeypatch):
    """Нет сайдкара / нет спикера / обработка выключена → None; включена → путь файла."""
    rendered = tmp_path / "cache.wav"
    rendered.write_bytes(b"RIFF processed voice")

    assert voicefx.final_voice_for_build(xml_path, cam1, emit=_noop) is None, \
        "сайдкара нарезки нет — спикера взять неоткуда"

    _write_sidecar(xml_path, cam1)
    assert voicefx.final_voice_for_build(xml_path, cam1, emit=_noop) is None, \
        "в сайдкаре нет спикера"

    _speaker(speakers_dir, denoise={"on": False}, vst=[])
    _write_sidecar(xml_path, cam1, speaker="Голос")
    assert voicefx.final_voice_for_build(xml_path, cam1, emit=_noop) is None, \
        "обработка выключена — сборка идёт со звуком камеры"

    # Старый профиль: галки сняты, шумодав включён — обработка работает (миграция)
    _speaker(speakers_dir, denoise={"on": True, "engine": "deepfilter", "atten_db": 40},
             cut=False, final=False)
    monkeypatch.setattr(voicefx, "render_cached",
                        lambda src, fx, emit=_noop, progress=None, cancelled=None,
                        pid_of=None: str(rendered))
    got = voicefx.final_voice_for_build(xml_path, cam1, emit=_noop)

    assert got == voicefx.final_voice_path(xml_path)
    assert os.path.isfile(got), "итоговый трек не запечён"


def test_final_voice_for_build_keeps_old_file_and_survives_failure(xml_path, cam1,
                                                                   speakers_dir, monkeypatch):
    """Обработка выключена — старый .voice.wav не трогаем; сбой рендера — лог и None."""
    _write_sidecar(xml_path, cam1, speaker="Голос")
    old = voicefx.final_voice_path(xml_path)
    with open(old, "wb") as f:
        f.write(b"RIFF old voice")
    _speaker(speakers_dir, denoise={"on": False}, vst=[])
    assert voicefx.final_voice_for_build(xml_path, cam1, emit=_noop) is None
    assert os.path.isfile(old), "старый итоговый трек удалён — вернуть его будет нечем"

    _speaker(speakers_dir, denoise={"on": True, "engine": "deepfilter", "atten_db": 40})
    monkeypatch.setattr(voicefx, "render_cached",
                        _raise(ReelsiError(umsg("voicefx_render_failed", "ffmpeg упал"))))
    lines = []
    got = voicefx.final_voice_for_build(xml_path, cam1,
                                        emit=lambda line="", **kw: lines.append(line))
    assert got is None, "сборка не должна падать из-за голоса"
    assert any("не подключён" in ln for ln in lines), "сбой не виден в логе"


def test_clip_final_fx_is_the_only_rule(xml_path, cam1, speakers_dir):
    """`clip_final_fx` — ОДНО правило «нужен ли клипу обработанный голос».

    По нему решают все двери (сборка AE, превью-прокси, читатели XML/DRP/черновика),
    поэтому правило проверяется по границам: нет сайдкара, нет спикера, пустая
    обработка (запекать было бы нечего — получилась бы копия звука камеры полным
    прогоном). Галок «в итоговый трек» в нём больше нет: включённая обработка — и
    есть ответ, а старые `cut`/`final=false` обработку не выключают.
    """
    _speaker(speakers_dir, denoise={"on": True, "engine": "deepfilter", "atten_db": 40})

    assert voicefx.clip_final_fx(xml_path) is None, "правило нашлось без сайдкара нарезки"
    _write_sidecar(xml_path, cam1)
    assert voicefx.clip_final_fx(xml_path) is None, "правило нашлось у клипа без спикера"

    _write_sidecar(xml_path, cam1, speaker="Голос")
    fx = voicefx.clip_final_fx(xml_path)
    assert fx is not None and fx["denoise"] == {"on": True, "engine": "deepfilter",
                                                "atten_db": 40, "mix": 100}, fx

    # Старые снятые галки — не решение: обработка настроена, значит голос нужен
    _speaker(speakers_dir, denoise={"on": True, "atten_db": 40}, cut=False, final=False)
    assert voicefx.clip_final_fx(xml_path) is not None, \
        "старый профиль с cut/final=false выключил обработку"

    # Обработка пуста: запекать нечего (это была бы копия звука камеры)
    _speaker(speakers_dir, denoise={"on": False, "atten_db": 40}, vst=[])
    assert voicefx.clip_final_fx(xml_path) is None, "пустая обработка считается за голос"

    # Профиль со включённым, но отсутствующим плагином — обработка есть
    _speaker(speakers_dir, denoise={"on": False},
             vst=[{"path": "C:/p.vst3", "on": True}])
    assert voicefx.clip_final_fx(xml_path) is not None, "включённый плагин не считается"


def test_final_voice_ready_needs_the_same_key(xml_path, cam1, tmp_path):
    """Готовность голоса — по ключу кеша в сайдкаре, а не по «файл лежит».

    Иначе после смены настроек подхватился бы трек, собранный под прежние: он лежит
    рядом с XML, и «файл есть» тут ничего не значит.
    """
    fx40 = voicefx.normalize_fx({"denoise": {"on": True, "atten_db": 40}, "final": True})
    fx70 = voicefx.normalize_fx({"denoise": {"on": True, "atten_db": 70}, "final": True})
    dst = voicefx.final_voice_path(xml_path)

    with open(dst, "wb") as f:
        f.write(b"RIFF processed voice")
    assert voicefx.final_voice_ready(xml_path, cam1, fx40) is False, "голос без сайдкара готов"

    meta = os.path.splitext(xml_path)[0] + ".voice.json"
    with open(meta, "w", encoding="utf-8") as f:
        json.dump({"key": voicefx.final_voice_key(cam1, fx40), "src": cam1}, f)
    assert voicefx.final_voice_ready(xml_path, cam1, fx40) is True
    assert voicefx.final_voice_ready(xml_path, cam1, fx70) is False, \
        "голос под другие настройки считается готовым"
    assert voicefx.final_voice_key(cam1, fx40) != voicefx.final_voice_key(cam1, fx70)


# --------------------------------------------------------------------------- #
# 3. Сборка AE: VOICE_WAV, аудиослой и прежний .jsx без обработанного голоса
# --------------------------------------------------------------------------- #
def _build(xml, tmp_path, name="out.jsx"):
    path, _nclips, _nsubs = xml2ae_build.to_ae_full(
        xml, jsx_path=str(tmp_path / name), style={}, inserts=[],
        disclaimer="", intro_riser=False, emit=_noop)
    return open(path, encoding="utf-8-sig").read()


def test_build_with_voice_wav_puts_audio_layer_on_camera1(xml_subs, tmp_path, monkeypatch):
    """Есть обработанный голос: VOICE_WAV, звук видео выключен, громкость и цензура на слое."""
    voice = tmp_path / "clip.voice.wav"
    voice.write_bytes(b"RIFF processed voice")
    monkeypatch.setattr(voicefx, "final_voice_for_build", lambda xml, cam, emit=_noop: str(voice))

    jsx = _build(xml_subs, tmp_path, name="voice.jsx")

    assert "var VOICE_WAV=%s;" % _js(str(voice)) in jsx, "нет объявления VOICE_WAV"
    assert 'vsrc = imp(VOICE_WAV); toBin(vsrc,"Звук");' in jsx, "голос не импортируется"
    assert "main.layers.add(vsrc)" in jsx, "аудиослой голоса не создаётся"
    assert "vl.parent=nul;" in jsx, "аудиослой не привязан к нулу камеры"
    assert jsx.count("lay.audioEnabled=false;") == 1, "звук видео камеры 1 не выключен"
    assert 'alv0=vl.property("ADBE Audio Group")' in jsx, "громкость осталась на видео"
    assert "censorLayer(vl)" in jsx, "цензура не переехала на аудиослой"
    # Тайминг слоя — ровно как у видеослоя клипа. Видимость (eye) НЕ копируем: у клипа
    # камеры 1 её выключают, когда в кадре камера 2, а голос в это время звучать обязан.
    assert not re.search(r"vl\.enabled", jsx), "видимость видеослоя скопирована в аудиослой"
    for bind in ("vl.startTime=lay.startTime;", "vl.inPoint=lay.inPoint;",
                 "vl.outPoint=lay.outPoint;"):
        assert bind in jsx, "аудиослой разъехался с видео: " + bind


def test_build_without_voice_is_byte_identical_to_golden(xml_subs, tmp_path):
    """Без обработанного голоса .jsx прежний байт в байт: подстановки пусты, voice_lay=lay."""
    jsx = _mask_assets(_golden_build(xml_subs, tmp_path))
    golden = _mask_assets(open(GOLDEN, encoding="utf-8-sig").read())
    assert jsx == golden, "сборка без обработанного голоса разошлась с эталоном"
    assert "VOICE_WAV" not in jsx and "vsrc" not in jsx and "censorLayer(lay)" in jsx


def test_plan_audio_voice_src_follows_processed_voice(xml_subs, tmp_path, monkeypatch):
    """plan["audio"]["voice_src"] — обработанный голос, а без него — звук камеры 1."""
    voice = tmp_path / "clip.voice.wav"
    voice.write_bytes(b"RIFF processed voice")
    monkeypatch.setattr(voicefx, "final_voice_for_build", lambda xml, cam, emit=_noop: str(voice))
    plan = xml2ae_build.scene_plan(xml_subs, style={}, inserts=[], disclaimer="",
                                   intro_riser=False, emit=_noop)
    assert plan["audio"]["voice_src"] == str(voice), "превью сыграет звук камеры, а не голос"

    monkeypatch.setattr(voicefx, "final_voice_for_build", lambda xml, cam, emit=_noop: None)
    plan = xml2ae_build.scene_plan(xml_subs, style={}, inserts=[], disclaimer="",
                                   intro_riser=False, emit=_noop)
    assert plan["audio"]["voice_src"] == plan["cams"][0]["path"], \
        "без обработанного голоса превью должно играть звук камеры 1"
