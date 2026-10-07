# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Обработанный голос камеры 1 — в Premiere XML и в черновом рендере.

У спикера клипа с ВКЛЮЧЁННОЙ обработкой голоса (шумодав или плагин цепочки — правило
одно, `core.voicefx.voice_fx_on`) рядом с XML лежит `<стем>.voice.wav` — обработанный
голос камеры 1 (шумодав + VST), той же длины и с тем же таймкодом, что звук камеры 1
(`core/voicefx.final_voice_path`). Сборка AE его уже использует. Правило одно и то же
у всех потребителей (`core.voicefx.clip_voice_wav`): **обработка включена и файл на
диске — звук камеры 1 берётся из него; иначе всё как раньше, байт в байт**. Здесь
проверяются Premiere XML (`xmlbuild.build`) и черновик (`draftrender.render_draft`),
которым этого не хватало: там звук шёл с файла камеры 1.

Отдельно — п. 3 задания: `file-voice` это АУДИО без видео, и потребители XML
(`parse_full`/`virtual_edl`/`scene_plan`, `verify_jsx --xml`) не должны видеть в нём
камеру: камеры определяются по ВИДЕО-дорожкам.

Имена тестов латиницей: `tmp_path` собирается из имени теста, кириллица в пути роняет
чтение stderr ffprobe (cp1252) — предупреждение в чужом коде, к правке не относящееся.

Запуск:  py -3.10 -m pytest tests/test_voice_export.py
"""
import json
import os
import re
import sys
import types
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

CAM_SEGS = [(0.0, 2.0), (4.0, 6.0)]
CAM_OFFSETS = [0.0, 0.0]
CAM_ASSIGN = [1, 0]              # первый кусок показывает камера 2, второй — камера 1
CLIP_FIELDS = ("start", "end", "in", "out")
SPEAKER = "Голос"


def _wav(path, seconds: float = 2.0, rate: int = 48000) -> str:
    """Пустой WAV на `seconds` секунд — так выглядит запечённый голос (wave-модуль)."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (int(rate * seconds) * 2 * 2))
    return str(path)


@pytest.fixture(autouse=True)
def speaker_on(tmp_path, monkeypatch):
    """Спикер клипа с ВКЛЮЧЁННОЙ обработкой голоса — в tmp_path, не в репозитории.

    Правило «нужен ли клипу обработанный голос» читает профиль спикера из сайдкара
    нарезки: без него (`test_2_clip_cams_ignores_voice_file` и половина `test_1_...`)
    читатели обязаны идти со звуком камеры, а с ним — брать запечённый трек.
    """
    from core import speakers
    d = tmp_path / "speakers"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(d))
    speakers.save(SPEAKER, {"label": SPEAKER,
                            "voice_fx": {"denoise": {"on": True, "engine": "deepfilter",
                                                     "atten_db": 40}, "vst": []}})
    return d


def _sidecar(xml, cam1) -> None:
    """Сайдкар нарезки рядом с XML — так его пишет нарезка (core.project_file)."""
    from core.project_file import write_project
    write_project(os.path.splitext(str(xml))[0] + ".project.json",
                  {"cams": [cam1], "speaker": SPEAKER})


@pytest.fixture(autouse=True)
def bake_stub(monkeypatch):
    """Печь голос здесь нечем (ffmpeg и модели не гоняем): «печь» = отдать готовый файл.

    Читатель (`clip_voice_wav`) сам печёт свежий трек, если файла нет или он под
    старые настройки. Подмена печёт «по-тестовому»: есть `.voice.wav` рядом с XML —
    он и есть запечённый, нет — сбой запекания (звук тогда идёт с камеры).
    """
    from core import voicefx

    def fake(xml_path, cam1, fx, **kw):
        dst = voicefx.final_voice_path(xml_path)
        if not os.path.isfile(dst):
            raise RuntimeError("печь нечем")
        return dst, dst

    monkeypatch.setattr(voicefx, "ensure_final_voice", fake)


@pytest.fixture
def cams(tmp_path, monkeypatch):
    """Две камеры. Пробы подменены: ffprobe и ffmpeg в тестах не гоняем.

    Сборка XML обёрнута: рядом с собранным XML ложится сайдкар со спикером — ровно
    как в бою (его пишет нарезка). Без сайдкара правило голоса не нашлось бы, и тест
    проверял бы не то, что собирался.
    """
    from core import media, xmlbuild

    monkeypatch.setattr(xmlbuild, "probe", lambda p, **k: {
        "dur_s": 60.0, "width": 1920, "height": 1080, "timecode": "01;00;00;00", "fps": 25.0})
    # Длину голоса `xmlbuild` спрашивает у media.probe_duration (ffprobe) — отдаём
    # длину фикстуры (2 с): <duration> файла обязан быть 2 * 60 = 120 кадров.
    monkeypatch.setattr(media, "probe_duration", lambda p: 2.0)
    real = xmlbuild.build

    def build(cam_paths, segments, offsets, out_path, **kw):
        _sidecar(out_path, cam_paths[0])
        return real(cam_paths, segments, offsets, out_path, **kw)

    monkeypatch.setattr(xmlbuild, "build", build)
    paths = [str(tmp_path / "cam1.mp4"), str(tmp_path / "cam2.mp4")]
    for p in paths:                    # читатель голоса сверяет, что камера 1 на диске
        Path(p).write_bytes(b"video")
    return paths


def _build(xmlbuild, cams, out) -> dict:
    return xmlbuild.build(cams, CAM_SEGS, CAM_OFFSETS, str(out), assign=CAM_ASSIGN)


def _refs(track) -> list:
    """Ссылки на файлы у клипов одной дорожки — в порядке клипов."""
    return [c.find(".//file").get("id") for c in track.findall("clipitem")]


def _tracks(text: str):
    """(видео-дорожки, аудио-дорожки) секвенции."""
    seq = ET.fromstring(text).find(".//sequence")
    return seq.findall("media/video/track"), seq.findall("media/audio/track")


# --------------------------------------------------------------------------- #
# 1. Premiere XML: звук камеры 1 — из обработанного голоса
# --------------------------------------------------------------------------- #
def test_1_xml_voice_refs_and_byte_identical_without_voice(tmp_path, cams):
    """С `.voice.wav` звук камеры 1 ссылается на file-voice, без файла XML прежний."""
    from core import xmlbuild

    out = tmp_path / "01_clip.xml"
    _build(xmlbuild, cams, out)
    base = out.read_bytes()                        # голоса на диске нет — XML прежний
    assert b"file-voice" not in base

    voice = _wav(tmp_path / f"{out.stem}.voice.wav")
    _build(xmlbuild, cams, out)                    # тот же вызов, файл на месте
    text = out.read_text(encoding="utf-8")

    vt, at = _tracks(text)
    assert _refs(vt[0]) == ["file-1", "file-1"], "видео камеры 1 остаётся на file-1"
    assert _refs(vt[1]) == ["file-2", "file-2"], "камера 2 не меняется"
    assert _refs(at[0]) == ["file-voice", "file-voice"], "звук камеры 1 — обработанный голос"
    assert _refs(at[1]) == ["file-2", "file-2"], "звук камеры 2 не трогаем"

    # определение одно: оно плюс (клипов − 1) ссылок на него = ровно число клипов
    assert text.count('<file id="file-voice">') == 1, "определение file-voice не одно"
    assert text.count('id="file-voice"') == len(at[0].findall("clipitem")), \
        "file-voice определён не один раз (определение + ссылки на него)"

    block = re.search(r'<file id="file-voice">.*?</file>', text, re.S)
    assert block, "в XML нет определения file-voice"
    assert "<video>" not in block.group(0), "file-voice — аудиофайл, видео у него нет"
    assert "<duration>120</duration>" in block.group(0), "длины файла (2 с) в определении нет"
    assert xmlbuild.pathurl(voice) in block.group(0), "в определении не путь voice.wav"

    # тайминг голоса тот же, что у звука камеры 1: те же start/end/in/out
    for v, a in zip(vt[0].findall("clipitem"), at[0].findall("clipitem")):
        assert [v.findtext(f) for f in CLIP_FIELDS] == [a.findtext(f) for f in CLIP_FIELDS], \
            "звук уехал от картинки: тайминги клипов не совпали"

    os.rename(voice, voice + ".off")               # файл временно убран
    _build(xmlbuild, cams, out)
    assert out.read_bytes() == base, "без .voice.wav XML не совпал с прежним байт в байт"


def test_1_xml_voice_keeps_video_and_other_cameras(tmp_path, cams):
    """Обратная сторона правила: голос камеры 1 не трогает ни видео, ни чужие дорожки."""
    from core import xmlbuild

    out = tmp_path / "02_clip.xml"
    _wav(tmp_path / f"{out.stem}.voice.wav")
    _build(xmlbuild, cams, out)
    text = out.read_text(encoding="utf-8")

    vt, at = _tracks(text)
    assert len(vt) == 2 and len(at) == 2, "число дорожек изменилось"
    for track in (vt[1], at[1]):                   # камеры 2..N целиком прежние
        assert set(_refs(track)) == {"file-2"}, "камера 2 подменилась на голос"
    assert set(_refs(vt[0])) == {"file-1"}, "видео камеры 1 подменилось на голос"
    assert "file-voice" not in ET.tostring(vt[0], encoding="unicode")


# --------------------------------------------------------------------------- #
# 2. Потребители XML: голос — не камера
# --------------------------------------------------------------------------- #
def test_2_xml_readers_do_not_treat_voice_as_camera(tmp_path, cams):
    """parse_full/virtual_edl/scene_plan: камер столько же, пути — видеофайлы."""
    from core import xml2ae, xmlbuild

    out = tmp_path / "01_clip.xml"
    _build(xmlbuild, cams, out)                    # без голоса
    edl_plain = xml2ae.virtual_edl(str(out))

    _wav(tmp_path / f"{out.stem}.voice.wav")
    _build(xmlbuild, cams, out)                    # с голосом
    xml = str(out)

    _meta, cam_tracks, subs, ins = xml2ae.parse_full(xml)
    assert len(cam_tracks) == 2, "file-voice посчитан камерой"
    assert [os.path.basename(c["path"]) for c in cam_tracks] == ["cam1.mp4", "cam2.mp4"]
    assert not subs and not ins

    edl = xml2ae.virtual_edl(xml)
    assert len(edl["cams"]) == 2, "file-voice попал в камеры черновика"
    assert [os.path.basename(c["path"]) for c in edl["cams"]] == ["cam1.mp4", "cam2.mp4"]
    assert edl["cams"] == edl_plain["cams"]
    assert edl["segs"] == edl_plain["segs"], "монтаж от голоса изменился"
    assert edl["audio"] == edl_plain["audio"], "тайминги звука от голоса изменились"
    assert edl["segs"] and edl["audio"]

    plan = xml2ae.scene_plan(xml)
    assert len(plan["cams"]) == 2, "file-voice попал в план сборки"
    assert [os.path.basename(c["path"]) for c in plan["cams"]] == ["cam1.mp4", "cam2.mp4"]


def test_2_clip_cams_ignores_voice_file(tmp_path):
    """`api.files._clip_cams` (без сайдкара читает `<pathurl>` из XML) — тоже не камера.

    Эта ветка собирает список исходников по `<file>`-блокам, и `file-voice` попадал в
    него наравне с камерами: роут удаления нарезки считал `<стем>.voice.wav`
    «исходником камеры» и оставлял его на диске навсегда.
    """
    from api import files as apifiles
    from core import xmlbuild

    cam1, cam2 = tmp_path / "cam1.mp4", tmp_path / "cam2.mp4"
    voice = tmp_path / "01_clip.voice.wav"
    xml = tmp_path / "01_clip.xml"
    # камера 2 — с ОДИНАРНЫМИ кавычками у id: так пишут XML руками и другие программы
    xml.write_text(
        "<xmeml><sequence><media><video><track><clipitem>"
        f'<file id="file-1"><name>cam1.mp4</name>'
        f"<pathurl>{xmlbuild.pathurl(str(cam1))}</pathurl></file>"
        f"<file id='f9'><name>cam2.mp4</name>"
        f"<pathurl>{xmlbuild.pathurl(str(cam2))}</pathurl></file>"
        "</clipitem></track></video><audio><track><clipitem>"
        f'<file id="{xmlbuild.VOICE_FILE_ID}"><name>voice</name>'
        f"<pathurl>{xmlbuild.pathurl(str(voice))}</pathurl></file>"
        f'<file id="{xmlbuild.VOICE_FILE_ID}"/>'
        "</clipitem></track></audio></media></sequence></xmeml>", encoding="utf-8")

    assert apifiles._clip_cams(str(xml)) == [str(cam1), str(cam2)], \
        "file-voice попал в список камер (или потерялась камера с одинарными кавычками)"


# --------------------------------------------------------------------------- #
# 3. Черновой рендер: аудиовход — обработанный голос
# --------------------------------------------------------------------------- #
def _inputs(cmd: list) -> list:
    """Пути входов ffmpeg (`-i <путь>`) в порядке аргументов."""
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]


@pytest.fixture
def draft(tmp_path, monkeypatch):
    """render_draft с подменённым EDL и ffmpeg; команды ffmpeg складываются в cmds."""
    from core import draftrender, xml2ae

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    xml = tmp_path / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    _sidecar(xml, str(clip))          # спикер клипа: без него правило голоса не найдётся
    monkeypatch.setattr(xml2ae, "virtual_edl", lambda p, ncams=None: {
        "segs": [{"ci": 0, "ts": 0.0, "te": 1.0, "src": 0.0}],
        "audio": [{"ts": 0.0, "te": 1.0, "src": 0.0}],
        "cams": [{"path": str(clip)}], "w": 1080, "h": 1920, "words": []})
    monkeypatch.setattr(draftrender, "hw_encoder", lambda refresh=False: None)
    cmds: list = []

    def fake_ff(cmd, **kw):
        cmds.append(cmd)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="подменено")

    monkeypatch.setattr(draftrender, "_run_ff", fake_ff)
    return draftrender, xml, tmp_path, cmds


def _draft(draftrender, xml) -> None:
    """Прогон черновика: ffmpeg подменён, потому сборка «не удалась» — это и ждём."""
    with pytest.raises(RuntimeError):
        draftrender.render_draft(str(xml), use_proxy=False, emit=lambda *a, **k: None)


def test_3_draft_audio_input_is_voice_when_file_exists(draft):
    """Есть `.voice.wav` — звук черновика берётся из него (аудиовход — он)."""
    draftrender, xml, tmp_path, cmds = draft

    _draft(draftrender, xml)                       # голоса нет — звук с камеры 1
    assert _inputs(cmds[-1]) == [str(tmp_path / "clip.mp4")]

    voice = _wav(tmp_path / "clip.voice.wav")
    _draft(draftrender, xml)                       # файл есть — аудиовход он
    inputs = _inputs(cmds[-1])
    assert inputs[0] == voice, f"аудиовход не голос: {inputs}"
    assert str(tmp_path / "clip.mp4") in inputs, "видео камеры 1 пропало из входов"
    script = (tmp_path / "_tmp" / "clip.draft_filters.txt").read_text(encoding="utf-8")
    assert "[0:a]atrim=" in script, f"звук берётся не с входа 0 (голоса):\n{script}"


def test_3_draft_audio_input_is_camera_without_voice(draft):
    """Нет файла — вход прежний (исходник камеры 1), как до правки."""
    draftrender, xml, tmp_path, cmds = draft

    _draft(draftrender, xml)
    assert _inputs(cmds[-1]) == [str(tmp_path / "clip.mp4")]
    script = (tmp_path / "_tmp" / "clip.draft_filters.txt").read_text(encoding="utf-8")
    assert "[0:a]atrim=" in script


# --------------------------------------------------------------------------- #
# 4. verify_jsx --xml на XML с file-voice
# --------------------------------------------------------------------------- #
def _write_jsx(tmp_path, cam_paths) -> str:
    """Минимальный .jsx с теми же камерами, что в XML, и без субтитров/вставок."""
    from core import verify_jsx

    parts = {
        "CAM": [{"path": p, "name": os.path.basename(p), "clips": [[0, 60, 0, 60, True, 100]]}
                for p in cam_paths],
        "SUBS": [], "ROTO": [], "INTRO_GROUPS": [[]], "INSERTS": [], "CAM1_SCALE": [],
    }
    lines = ["// Reelsi -> After Effects FULL build (auto-generated)"]
    for key in verify_jsx.WANTED:
        lines.append("    var %s=%s;" % (key, json.dumps(parts[key], ensure_ascii=True,
                                                         separators=(",", ":"))))
    lines.append("app.beginUndoGroup('Reelsi');")
    p = tmp_path / "build.jsx"
    p.write_text("\n".join(lines), encoding="utf-8-sig")
    return str(p)


def test_4_verify_jsx_with_voice_xml_is_clean(tmp_path, cams):
    """Сверка .jsx с XML, где звук камеры 1 — file-voice, не ругается на камеры."""
    from core import verify_jsx, xmlbuild

    for path in cams:                              # verify_jsx ругается на пропавшее медиа
        Path(path).write_bytes(b"\x00")

    out = tmp_path / "01_clip.xml"
    _wav(tmp_path / f"{out.stem}.voice.wav")
    _build(xmlbuild, cams, out)

    rep = verify_jsx.verify(_write_jsx(tmp_path, cams), xml_path=str(out))
    assert not [e for e in rep.errors if "камер" in e], rep.errors
    assert not [e for e in rep.errors if "parse_full" in e], rep.errors
    assert not rep.errors, rep.errors


# --------------------------------------------------------------------------- #
# 5. Синхронизация голоса в XML и экспорт
# --------------------------------------------------------------------------- #
def test_sync_xml_voice_roundtrip(tmp_path, cams):
    """Синхронизация дорожки звука в XML: с файлом — file-voice, без файла — file-1."""
    from core import xmlbuild

    out = tmp_path / "sync_clip.xml"
    _build(xmlbuild, cams, out)
    text = out.read_text(encoding="utf-8")
    _vt, at = _tracks(text)
    assert _refs(at[0]) == ["file-1", "file-1"]
    assert "file-voice" not in text

    voice = Path(_wav(tmp_path / f"{out.stem}.voice.wav"))
    res = xmlbuild.sync_xml_voice(str(out))
    assert res is True
    text = out.read_text(encoding="utf-8")
    _vt, at = _tracks(text)
    assert _refs(at[0]) == ["file-voice", "file-voice"]
    assert '<file id="file-voice">' in text

    block = re.search(r'<file id="file-voice">.*?</file>', text, re.S)
    assert block
    assert "<pathurl>" in block.group(0)
    assert "<rate>" in block.group(0)
    assert "<timebase>" in block.group(0)
    assert "<duration>" in block.group(0)

    voice.unlink()
    res2 = xmlbuild.sync_xml_voice(str(out))
    assert res2 is True
    text = out.read_text(encoding="utf-8")
    _vt, at = _tracks(text)
    assert _refs(at[0]) == ["file-1", "file-1"]
    assert '<file id="file-voice">' not in text


def test_api_export_xml_with_voice(tmp_path, cams):
    """Эндпоинт /api/export_xml отдаёт XML с file-voice при наличии voice.wav."""
    from flask import Flask
    import api
    from core import xmlbuild

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    client = app.test_client()

    out = tmp_path / "export_clip.xml"
    _build(xmlbuild, cams, out)

    _wav(tmp_path / f"{out.stem}.voice.wav")

    r = client.post("/api/export_xml", json={"path": str(out)},
                    headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    xml_data = r.data.decode("utf-8")
    assert "file-voice" in xml_data
    _vt, at = _tracks(xml_data)
    assert _refs(at[0]) == ["file-voice", "file-voice"]
