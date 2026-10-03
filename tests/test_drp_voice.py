# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Обработанный голос камеры 1 в экспорте DaVinci Resolve (`.drp`).

У спикера с флагом `voice_fx.final` рядом с XML лежит `<стем>.voice.wav` —
обработанный голос камеры 1 (шумодав + VST), той же длины и на той же шкале
времени, что звук камеры 1 (`core/voicefx.final_voice_path`). В XML для Premiere и
в `.jsx` звук камеры 1 уже берётся из него; здесь проверяется то же правило для
`.drp`.

Запись медиапула — ПОЛНЫЙ дескриптор файла, и звук в ней отдельный: свой `<Clip>`
(путь, имя, mtime, кодек) внутри `<BtAudioInfo>` и своя `<TracksBA>`
(`SampleRate`, `NumChannels`, длительность в сэмплах). Подменяем ровно их: видео
остаётся камерой, клипы таймлайна не меняются — голос посчитан по звуку камеры 1,
и тайминг у него тот же. Файла нет (или он не читается) — `.drp` прежний.

UUID в сборке случайны (`new_ids`/`new_pool_ids`), поэтому там, где нужна сверка
БАЙТ В БАЙТ, они заморожены: иначе две сборки не сравнить.

Запуск:  py -3.10 -m pytest tests/test_drp_voice.py
"""
import os
import re
import struct
import sys
import uuid
import wave
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import drp  # noqa: E402

pytest.importorskip("zstandard")

# Пробы камер: ffprobe в тестах не гоняем — дескрипторы собираются из них.
CAM1 = {"dur_s": 120.0, "timecode": "01;00;00;00", "fps": 29.97, "width": 3840, "height": 2160}
CAM2 = {"dur_s": 100.0, "timecode": "02;00;00;00", "fps": 29.97, "width": 1920, "height": 1080}
SEGMENTS = [(0.0, 2.0), (4.0, 6.0)]


def _wav(path: Path, seconds: float = 4.0, rate: int = 48000, channels: int = 2) -> str:
    """WAV заданной длины — так выглядит запечённый голос (пишет его core/voicefx)."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * (round(rate * seconds) * channels * 2))
    return str(path)


@pytest.fixture
def cams(tmp_path):
    """Две камеры: пути настоящие (в tmp_path), файлов на диске нет — пробу подменяем."""
    return [str(tmp_path / "A.MP4"), str(tmp_path / "B.MP4")]


class _FrozenIds:
    """Замороженные UUID: в каждой сборке один и тот же ряд.

    UUID в сборке случайны (`new_ids`/`new_pool_ids`), и без заморозки две сборки
    не сравнить даже там, где меняться нечему. `reset` вызывается перед сборкой —
    счётчик общий на тест, а ряд обязан начинаться сначала.
    """

    def __init__(self) -> None:
        self._n = 0

    def reset(self) -> None:
        self._n = 0

    def __call__(self) -> uuid.UUID:
        self._n += 1
        return uuid.UUID(int=self._n)


@pytest.fixture
def frozen(monkeypatch) -> _FrozenIds:
    ids = _FrozenIds()
    monkeypatch.setattr(uuid, "uuid4", ids)
    return ids


def _build(out: Path, cams, voice: str | None = None, ids: _FrozenIds | None = None) -> dict:
    if ids is not None:
        ids.reset()            # ряд идентификаторов с начала: иначе сборки не сравнить
    drp.build(str(out), cams, SEGMENTS, [0.0, 0.0],
              probe=lambda p: dict(CAM1 if p == cams[0] else CAM2), voice=voice)
    return drp.read(str(out))


def _records(files: dict) -> list:
    """Записи медиапула в порядке сборки: первая — камера 1 (так их и кладёт build)."""
    mp = files["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    return re.findall(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>", mp, re.S)


def _clip(rec: str, tag: str) -> bytes:
    """protobuf-блоб `<Clip>` из блока `<BtVideoInfo>` (видео) или `<BtAudioInfo>` (звук)."""
    block = re.search(r"<" + tag + r"\b.*?</" + tag + r">", rec, re.S).group(0)
    blob = [h for h in re.findall(r"<Clip>([0-9a-f]{40,})</Clip>", block) if drp.is_zstd_blob(h)]
    assert blob, f"в <{tag}> нет zstd-блоба <Clip>"
    return drp.unpack_fields(blob[0])[1]


def _tracks(rec: str) -> bytes:
    return bytes.fromhex(re.search(r"<TracksBA>([0-9a-f]+)</TracksBA>", rec).group(1))


def _num(tracks: bytes, key: str) -> int:
    raw = drp.kv_get(tracks, key)
    assert raw is not None, f"в <TracksBA> нет ключа {key}"
    return int.from_bytes(raw, "big")


def _without_audio(rec: str) -> str:
    """Запись без звукового дескриптора — «всё, кроме звука»."""
    return re.sub(r"<BtAudioInfo\b.*?</BtAudioInfo>", "", rec, flags=re.S)


# --------------------------------------------------------------------------- #
# 1. Голос подменяет звуковой дескриптор записи камеры 1
# --------------------------------------------------------------------------- #
def test_voice_replaces_audio_clip_of_camera_1(tmp_path, cams):
    """Видео камеры 1 остаётся камерой, звук указывает на `.voice.wav`."""
    voice = _wav(tmp_path / "A.voice.wav", seconds=4.0)
    rec1, rec2 = _records(_build(tmp_path / "out.drp", cams, voice))

    v = _clip(rec1, "BtVideoInfo")
    assert drp.pb_get_str(v, 1) == str(tmp_path) and drp.pb_get_str(v, 2) == "A.MP4", \
        "видео камеры 1 подменилось голосом"

    a = _clip(rec1, "BtAudioInfo")
    assert drp.pb_get_str(a, 1) == str(tmp_path), "в звуковом <Clip> не путь голоса"
    assert drp.pb_get_str(a, 2) == "A.voice.wav", "звук камеры 1 не ссылается на голос"
    assert drp.pb_get_str(a, 3) == "Thu Jan 01 00:00:00 2026", "у голоса нет mtime"
    assert drp.pb_get_str(a, 5) == drp.VOICE_CODEC, "кодек голоса не PCM"

    # камера 2 целиком прежняя: и видео, и звук — её собственный файл
    assert (drp.pb_get_str(_clip(rec2, "BtVideoInfo"), 2),
            drp.pb_get_str(_clip(rec2, "BtAudioInfo"), 2)) == ("B.MP4", "B.MP4")


def test_wav_parameters_go_to_tracks_ba(tmp_path, cams):
    """`SampleRate`/`NumChannels`/длительность — ИЗ ГОЛОСА, а не из шаблона.

    Проверяется частота вне шаблонной (шаблон 48 кГц, стерео): иначе подмена
    SampleRate была бы неотличима от «ничего не делали».
    """
    voice = _wav(tmp_path / "A.voice.wav", seconds=2.5, rate=44100, channels=1)
    rec1 = _records(_build(tmp_path / "out.drp", cams, voice))[0]

    t = _tracks(rec1)
    assert _num(t, "SampleRate") == 44100, "частота взята не из WAV"
    assert _num(t, "NumChannels") == 1, "каналы взяты не из WAV"
    assert _num(t, "Duration") == round(2.5 * 44100), "длительность не в сэмплах WAV"
    # шкала времени остаётся камерной: клипы таймлайна берут из неё свои In
    start = struct.unpack(">d", drp.kv_get(t, "StartTime"))[0]
    assert start == pytest.approx(drp.timecode_seconds(CAM1["timecode"], CAM1["fps"])), \
        "StartTime звука перестал быть стартовым таймкодом камеры"


def test_ids_stay_linked_with_voice(tmp_path, cams):
    """`MediaRef` внутри блоба — это `DbId` элемента `<BtAudioInfo>`.

    Подмена звукового дескриптора не должна рвать эту связь: с разошедшимся
    `MediaRef` клипы в Resolve зелёные, но без волны и без звука.
    """
    voice = _wav(tmp_path / "A.voice.wav")
    files = _build(tmp_path / "ids.drp", cams, voice)
    mp = files["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    seq = files[drp.seq_name(files)].decode("utf-8")

    checked = 0
    for rec in _records(files):
        info = re.search(r'<BtAudioInfo DbId="(' + drp.GUID + r')"', rec)
        blob = re.search(r"<FieldsBlob>([0-9a-f]{200,})</FieldsBlob>", rec)
        assert info and blob, "у записи пропал <BtAudioInfo> или <FieldsBlob>"
        ref = drp.kv_get(drp.unpack_fields(blob.group(1))[1], "MediaRef")
        assert ref.decode("utf-16-be") == info.group(1), "MediaRef разошёлся с BtAudioInfo"
        checked += 1
    assert checked == 2

    pool = set(re.findall(r'<Sm2MpVideoClip DbId="([0-9a-f-]+)"', mp))
    refs = set(re.findall(r"<MediaRef>([0-9a-f-]+)</MediaRef>", seq))
    assert refs and refs <= pool, "клип таймлайна ссылается не на запись медиапула"
    ids = re.findall(r'DbId="([0-9a-f-]+)"', mp + seq)
    assert len(ids) == len(set(ids)), "DbId повторились"


# --------------------------------------------------------------------------- #
# 2. Файла нет — .drp прежний, байт в байт
# --------------------------------------------------------------------------- #
def test_no_voice_build_is_byte_identical(tmp_path, cams, frozen):
    """Без `.voice.wav` в `.drp` не меняется ни один байт.

    Прогонов три: вовсе без аргумента, с путём, которого нет, и с нечитаемым
    файлом. Все три обязаны совпасть; сборка с настоящим голосом — отличаться.
    """
    base = tmp_path / "a.drp"
    _build(base, cams, ids=frozen)
    reference = base.read_bytes()

    _build(tmp_path / "b.drp", cams, voice=str(tmp_path / "нет-такого.voice.wav"), ids=frozen)
    assert (tmp_path / "b.drp").read_bytes() == reference, \
        "путь к отсутствующему голосу изменил .drp"

    broken = tmp_path / "broken.voice.wav"
    broken.write_bytes("это не WAV вовсе".encode("utf-8"))
    assert drp.wav_audio_info(str(broken)) is None, "битый файл прочитался как WAV"
    _build(tmp_path / "c.drp", cams, voice=str(broken), ids=frozen)
    assert (tmp_path / "c.drp").read_bytes() == reference, \
        "нечитаемый голос изменил .drp"

    _wav(tmp_path / "A.voice.wav")
    _build(tmp_path / "d.drp", cams, voice=str(tmp_path / "A.voice.wav"), ids=frozen)
    assert (tmp_path / "d.drp").read_bytes() != reference, "голос не попал в .drp вовсе"


def test_voice_touches_only_camera_1_audio(tmp_path, cams, frozen):
    """Разница ровно одна: звуковой дескриптор записи камеры 1.

    Таймлайн, вторая камера и всё остальное в записи камеры 1 (видео, <Time>,
    <Geometry>) обязаны остаться прежними — иначе голос утащил бы за собой
    тайминг, а он у голоса тот же, что у звука камеры.
    """
    before_files = _build(tmp_path / "a.drp", cams, ids=frozen)
    _wav(tmp_path / "A.voice.wav")
    after_files = _build(tmp_path / "b.drp", cams, voice=str(tmp_path / "A.voice.wav"),
                         ids=frozen)

    sname = drp.seq_name(before_files)
    assert before_files[sname] == after_files[sname], "правка задела клипы таймлайна"

    before, after = _records(before_files), _records(after_files)
    assert before[1] == after[1], "подменилась запись камеры 2"
    assert before[0] != after[0], "запись камеры 1 не изменилась"
    assert _without_audio(before[0]) == _without_audio(after[0]), \
        "у камеры 1 изменилось что-то кроме звукового дескриптора"


def test_set_voice_descriptor_keeps_record_on_broken_input(tmp_path):
    """Прямая проверка отката: нечего подменять — запись возвращается как была."""
    rec = _records(_build(tmp_path / "a.drp", [str(tmp_path / "A.MP4")]))[0]
    missing = str(tmp_path / "нет.wav")
    assert drp.set_voice_descriptor(rec, missing) == rec
    assert drp.wav_audio_info(missing) is None
    assert drp.wav_audio_info(os.path.join(str(ROOT), "requirements.txt")) is None
