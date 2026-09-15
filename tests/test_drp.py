# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контейнер композиции DaVinci Resolve (`drp.py`).

Фикстура синтетическая: настоящий .drp нести в репозиторий нельзя (внутри
абсолютные пути и имена), а формат описан в `DRP_SPEC.md` достаточно точно,
чтобы собрать контейнер той же формы.

Главное, что здесь проверяется, — поле длины данных. Именно из-за него первая
рабочая версия давала в Resolve чёрный слой вместо титра: текст менялся, размер
полезной нагрузки уезжал, а поле оставалось старым.
"""
import os
import re
import struct
import sys
import zlib

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from core import drp
import public_slice  # noqa: E402

NODES = ('{ Text1 = TextPlus { Inputs = { StyledText = Input { Value = "СЛОВО", }, '
         'Font = Input { Value = "Open Sans", } } } }')
COMP = b'nComposition { CurrentTime = 158, RenderRange = { 0, 299 }, ' \
       b'GlobalRange = { 0, 299 }, Unsorted = { GlobalEnd = 299 }, Compressed = true, }\x00'

def _p(*parts):
    r"""Путь к медиа, родной для текущей ОС (C:\... на Windows, /... на Linux)."""
    return os.path.join(os.path.abspath(os.sep), *parts)


def make_blob(comp=COMP, nodes=NODES):
    """Собрать контейнер той же формы, что пишет Resolve."""
    payload = comp + len(nodes.encode()).to_bytes(4, "little") + zlib.compress(nodes.encode(), 9)
    prefix = (b"\x00\x00\x00\x01"
              + len(drp.KEY_DATA).to_bytes(4, "big") + drp.KEY_DATA
              + b"\x00\x00\x00\x0c" + b"\x00"
              + len(payload).to_bytes(4, "big"))
    outer = prefix + payload
    return (len(outer).to_bytes(4, "big") + zlib.compress(outer, 9)).hex()


def test_разбор_возвращает_текст_и_ноды():
    _, comp, nodes, _ = drp.split_comp(make_blob())
    assert comp == COMP
    assert nodes.decode() == NODES


def test_тождественная_перепаковка_побайтово():
    """Разобрать и собрать без правок -> распакованное совпадает."""
    blob = make_blob()
    prefix, comp, nodes, off = drp.split_comp(blob)
    again = drp.join_comp(prefix, comp, nodes, off)
    assert zlib.decompress(bytes.fromhex(again)[4:]) == zlib.decompress(bytes.fromhex(blob)[4:])


def test_длина_данных_пересчитывается():
    """Тот самый баг: текст стал длиннее — поле длины обязано поехать следом,
    иначе Resolve покажет чёрный слой."""
    prefix, comp, nodes, off = drp.split_comp(make_blob())
    longer = drp.set_text(nodes.decode(), "О" * 200).encode()
    out = zlib.decompress(bytes.fromhex(drp.join_comp(prefix, comp, longer, off))[4:])
    start = off + 4
    assert int.from_bytes(out[off:off + 4], "big") == len(out) - start
    drp.split_comp(drp.join_comp(prefix, comp, longer, off))   # не должно бросить


def test_битый_контейнер_ловится():
    prefix, comp, nodes, off = drp.split_comp(make_blob())
    buf = bytearray(prefix)
    buf[off:off + 4] = (12345).to_bytes(4, "big")            # соврали про длину
    outer = bytes(buf) + comp + len(nodes).to_bytes(4, "little") + zlib.compress(nodes, 9)
    bad = (len(outer).to_bytes(4, "big") + zlib.compress(outer, 9)).hex()
    with pytest.raises(ValueError, match="контейнер повреждён"):
        drp.split_comp(bad)


def test_замена_текста():
    assert 'Value = "ДРУГОЕ"' in drp.set_text(NODES, "ДРУГОЕ")


def test_кавычки_в_слове_не_ломают_синтаксис():
    """Lua-строка оборвалась бы на двойной кавычке."""
    assert '"' not in drp.set_text(NODES, 'ПРО"ВЕР"КА').split('Value = "')[1].split('"')[0]


def test_вход_добавляется_если_его_нет():
    """Fusion не пишет значения по умолчанию: у белого титра Red1 отсутствует."""
    assert "Red1" not in NODES
    out = drp.set_input(NODES, "Red1", "1.000000")
    assert "Red1 = Input { Value = 1.000000, }" in out


def test_существующий_вход_заменяется_а_не_дублируется():
    out = drp.set_input(drp.set_input(NODES, "Size", "0.05"), "Size", "0.09")
    assert out.count("Size = Input") == 1 and "0.09" in out


def test_длительность_в_timemap():
    assert drp.timemap(299) == "02" + struct.pack(">d", 299 / 60).hex()
    assert struct.unpack(">d", bytes.fromhex(drp.timemap(300)[2:]))[0] == 5.0


def test_timemap_медиаклипа_пять_чисел():
    """У медиаклипа карта описывает ИСХОДНИК, а не кусок: пять doubles, одинаковых
    для всех клипов камеры. Если поставить длительность клипа (как у титра),
    картинка в Resolve застывает. Эталон снят с рабочего проекта."""
    h = drp.media_timemap(163.165)                     # 4890 кадров @29.97
    assert h == "0240645eeeeeeeeeef00000000000000004064" \
                "5f7777777777000000000000000040645eeeeeeeeeef"
    b = bytes.fromhex(h)
    vals = [struct.unpack(">d", b[1 + 8 * i:9 + 8 * i])[0] for i in range(5)]
    assert vals[0] == vals[4] == pytest.approx(4889 / 30)
    assert vals[1] == vals[3] == 0.0
    assert vals[2] == pytest.approx(9779 / 60)


def test_старт_клипа_и_медиапула_в_разных_единицах():
    """Одна величина, две единицы: у клипа таймлайна кадры делятся на номинальные
    30, у MediaExtents медиапула — на реальные 29.97. Перепутать = уехать на 7 с."""
    assert drp.timecode_frames("01;57;31;11") == 211329
    assert drp.media_start_time("01;57;31;11") == pytest.approx(7044.3)
    assert drp.timecode_seconds("01;57;31;11") == pytest.approx(7051.3443, abs=1e-4)


def test_pal_25fps_своя_математика_таймкода():
    """Баг 2026-08-07: drp.py был зашит под NTSC 29.97, а джаггер-камеры пишут
    25 fps (PAL). От этого в .drp уезжали NumFrames, MediaStartTime и timemap —
    Resolve показывал не тот кадр внутри клипа (звук при этом был верен).
    У PAL номинал совпадает с частотой, счёт без drop-frame: `;` у ffprobe —
    запись формата, а не признак drop."""
    assert drp.timecode_frames("00;38;52;08", 25) == 58308
    assert drp.media_start_time("00;38;52;08", 25) == pytest.approx(2332.32)
    assert drp.timecode_seconds("00;38;52;08", 25) == pytest.approx(2332.32)
    assert round(139.68 * 25) == 3492                 # NumFrames, как в ffprobe
    vals = [struct.unpack(">d", bytes.fromhex(
        drp.media_timemap(139.68, 25))[1 + 8 * i:9 + 8 * i])[0] for i in range(5)]
    assert vals[0] == vals[4] == pytest.approx(3491 / 25)


def test_pal_25fps_не_путается_с_drop_frame():
    """Точка с запятой в PAL-таймкоде НЕ значит drop-frame: счётчик 25 fps не
    пропускает кадры, в отличие от NTSC 29.97."""
    assert drp.timecode_frames("00;38;52;08", 25) == 38 * 60 * 25 + 52 * 25 + 8
    assert drp.timecode_frames("00;38;52;08", 25) == drp.timecode_frames("00:38:52:08", 25)


def test_pal_25fps_framerate_в_пуле_и_клипе(tmp_path):
    """Баг 2026-08-07: FrameRate пула (29.97) и MediaFrameRate клипа (30.0)
    зашиты в шаблоне под NTSC и не правились — Resolve раскладывал кадры
    видео-клипа по 29.97/30, и картинка не совпадала с нарезкой (звук по
    времени был верен). Для PAL-камеры оба поля должны стать 25."""
    pytest.importorskip("zstandard")
    out = str(tmp_path / "pal.drp")
    pr = {"dur_s": 139.68, "timecode": "00;38;52;08", "width": 1920, "height": 1080,
          "fps": 25.0}
    drp.build(out, [_p("m", "a", "A.MP4")], [(0.0, 1.0)], [0.0], probe=lambda p: dict(pr))
    files = drp.read(out)
    seq = files[drp.seq_name(files)].decode("utf-8")
    mp = files["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    vc = re.search(r"<Sm2TiVideoClip\b.*?</Sm2TiVideoClip>", seq, re.S).group(0)
    mfr = bytes.fromhex(re.search(r"<MediaFrameRate>([0-9a-f]+)</MediaFrameRate>", vc).group(1))
    assert struct.unpack("<d", mfr[:8])[0] == pytest.approx(25.0)
    rec = re.search(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>", mp, re.S).group(0)
    t = bytes.fromhex(re.search(r"<Time>([0-9a-f]+)</Time>", rec).group(1))
    fr = drp.kv_get(t, "FrameRate")
    assert struct.unpack("<d", fr[:8])[0] == pytest.approx(25.0)


def test_раскладка_мультикама_во_флагах(tmp_path):
    """Flags=2 — клип выключен. Так Resolve хранит мультикам: камера 1 всегда
    видна, камера 2 — только на своих кусках. Без этого вторая камера закрывает
    первую на всём ролике (симптом: «застывшая картинка»)."""
    pytest.importorskip("zstandard")
    out = str(tmp_path / "mc.drp")
    drp.build(out, [_p("m", "a.MP4"), _p("m", "b.MP4")],
              [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0)], [0.0, 0.0],
              assign=[0, 1, 1, 0], probe=lambda p: dict(FAKE))
    files = drp.read(out)
    seq = files[drp.seq_name(files)].decode("utf-8")
    vec = re.search(r"<VideoTrackVec>(.*?)</VideoTrackVec>", seq, re.S).group(1)
    tracks = re.findall(r"<Element>\s*<Sm2TiTrack\b.*?</Sm2TiTrack>\s*</Element>", vec, re.S)
    def flags(track):
        """Именно у клипов: у самой дорожки тоже есть <Flags>, и она идёт раньше."""
        return [re.search(r"<Flags>(\d+)</Flags>", c).group(1)
                for c in re.findall(r"<Sm2TiVideoClip\b.*?</Sm2TiVideoClip>", track, re.S)]

    assert flags(tracks[0]) == ["0", "0", "0", "0"]    # камера 1 — база
    assert flags(tracks[1]) == ["2", "0", "0", "2"]    # камера 2 — по assign


def test_диапазоны_композиции():
    c = drp.retime_comp(COMP.decode("utf-8", "replace"), 24)
    assert "RenderRange = { 0, 23 }" in c and "GlobalRange = { 0, 23 }" in c
    assert "GlobalEnd = 23" in c and "CurrentTime = 0" in c


def test_таймкод_drop_frame_в_секунды():
    """01;57;31;11 -> 7051.34 с. Так это лежит в MediaExtents настоящего проекта;
    без поправки на drop-frame вышло бы 7058 — расхождение в семь секунд."""
    assert drp.timecode_seconds("01;57;31;11") == pytest.approx(7051.34, abs=0.05)


def test_таймкод_без_drop_frame():
    """Двоеточия = non-drop: пропусков в счётчике нет."""
    ndf = drp.timecode_seconds("01:57:31:11")
    assert ndf == pytest.approx(7058.42, abs=0.05)
    assert ndf > drp.timecode_seconds("01;57;31;11")


def test_таймкод_нули():
    assert drp.timecode_seconds("00;00;00;00") == 0.0


def test_media_extents_туда_и_обратно():
    pytest.importorskip("zstandard")
    payload = (b"\x00" * 8 + len(drp.KEY_EXTENTS).to_bytes(4, "big") + drp.KEY_EXTENTS
               + b"\x00\x00\x00\x0c" + b"\x00" + (16).to_bytes(4, "big")
               + struct.pack("<dd", 1.0, 2.0))
    head = b"\x00\x00\x00\x02\x00\x00\x00\x00\x81"
    blob = drp.pack_fields(head, payload)
    assert drp.is_zstd_blob(blob)
    assert drp.get_media_extents(blob) == (1.0, 2.0)
    out = drp.set_media_extents(blob, 7051.344, 163.165)
    assert drp.get_media_extents(out) == (7051.344, 163.165)


def test_шапка_блоба_держит_длину():
    """Второе поле шапки = длина блоба минус 8; иначе Resolve не читает запись."""
    pytest.importorskip("zstandard")
    blob = drp.pack_fields(b"\x00\x00\x00\x02\x00\x00\x00\x00\x81", b"x" * 400)
    raw = bytes.fromhex(blob)
    assert int.from_bytes(raw[4:8], "big") == len(raw) - 8


def test_блоб_без_extents_ругается():
    pytest.importorskip("zstandard")
    blob = drp.pack_fields(b"\x00\x00\x00\x02\x00\x00\x00\x00\x81", "нет ключа".encode())
    with pytest.raises(ValueError, match="MediaExtents"):
        drp.set_media_extents(blob, 0.0, 1.0)


# --- шаблон в репозитории ----------------------------------------------------

PERSONAL = ["C:\\Users", "камера", "Камера", "Desktop",
            "C1412", "C1410", "Videos", "CacheClip"]


def test_шаблон_обезличен():
    """Личное в .drp прячется под zstd и zlib — ни глазами, ни `git grep` его не
    видно. Проверка разворачивает ЛЮБОЙ hex-блоб, а не только известные теги:
    на этом уже попались — путь к файлу лежал в protobuf-блобе <Clip> внутри
    записи медиапула, и первая очистка его не тронула."""
    pytest.importorskip("zstandard")
    words = list(PERSONAL)
    words_path = os.path.join(ROOT, public_slice.WORDS_FILE)
    if os.path.exists(words_path):
        try:
            with open(words_path, encoding="utf-8") as f:
                words.extend(public_slice.parse_words(f.read()))
        except OSError:
            pass
    files = drp.read(drp.TEMPLATE)
    found = set()
    for name, raw in files.items():
        txt = raw.decode("utf-8", "replace")
        layers = [raw]
        for h in re.findall(r">([0-9a-f]{40,})<", txt):
            try:
                layers.append(drp.unpack_fields(h)[1] if drp.is_zstd_blob(h)
                              else bytes.fromhex(h))
            except Exception:
                pass
        for h in re.findall(r"<CompositionBA>([0-9a-f]+)</CompositionBA>", txt):
            try:
                _, comp, nodes, _ = drp.split_comp(h)
                layers += [comp, nodes]
            except Exception:
                pass
        for blob in layers:
            for enc in ("utf-8", "utf-16-be"):
                s = blob.decode(enc, "replace")
                found |= {w for w in words if w in s}
    assert not found, f"в шаблоне осталось личное: {sorted(found)}"


# --- сборка проекта из шаблона ----------------------------------------------

FAKE = {"dur_s": 163.165, "timecode": "01;57;31;11", "width": 3840, "height": 2160}


@pytest.fixture
def built(tmp_path):
    """Проект на двух камерах, трёх кусках и трёх словах. probe подменён — тесту
    не нужен ни ffprobe, ни настоящее медиа."""
    pytest.importorskip("zstandard")
    out = str(tmp_path / "out.drp")
    info = drp.build(out, [_p("media", "a", "A.MP4"), _p("media", "b", "B.MP4")],
                     [(0.0, 1.0), (2.0, 3.0), (4.0, 6.0)], [0.0, 0.5],
                     sub_words=[(0, 10, "РАЗ"), (12, 20, "ДВА"), (24, 40, "ТРИ")],
                     yellow=[1], name="TEST", probe=lambda p: dict(FAKE))
    return out, info, drp.read(out)


def test_сборка_считает_клипы(built):
    _, info, _ = built
    assert info["cameras"] == 2 and info["clips"] == 6 and info["subtitles"] == 3
    assert info["total_frames"] == 60 + 60 + 120


def test_дорожки_камеры_потом_титры(built):
    """Раскладка снизу вверх — её ждёт parse_full."""
    _, _, files = built
    seq = files[drp.seq_name(files)].decode("utf-8")
    vec = re.search(r"<VideoTrackVec>(.*?)</VideoTrackVec>", seq, re.S).group(1)
    tracks = re.findall(r"<Element>\s*<Sm2TiTrack\b.*?</Sm2TiTrack>\s*</Element>", vec, re.S)
    assert len(tracks) == 3
    assert [len(re.findall(r"<Sm2TiVideoClip ", t)) for t in tracks] == [3, 3, 3]


def test_dbid_уникальны_на_весь_проект(built):
    """Дубль DbId — и Resolve теряет клипы."""
    _, _, files = built
    ids = []
    for name in (drp.seq_name(files), "MediaPool/Master/MpFolder.xml"):
        ids += re.findall(r'DbId="([0-9a-f-]+)"', files[name].decode("utf-8"))
    assert len(ids) == len(set(ids))


def test_личность_записей_медиапула_различается(built):
    """Баг: клонировали запись и меняли только DbId. Свою личность запись хранит
    ещё в <UniqueMediaPoolItemId> и в GUID внутри zstd-блоба; при совпадении
    Resolve считает обе камеры одним медиа — первая уходит в офлайн, вторая
    показывает застывший кадр, звук есть, но не играет."""
    _, _, files = built
    mp = files["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    dbids, uniques, inner = [], [], []
    for b in re.findall(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>", mp, re.S):
        dbids.append(re.search(r'DbId="(' + drp.GUID + r')"', b).group(1))
        uniques.append(re.search(r"<UniqueMediaPoolItemId>(" + drp.GUID + r")<", b).group(1))
        h = re.search(r"<FieldsBlob>([0-9a-f]{200,})</FieldsBlob>", b).group(1)
        data = drp.unpack_fields(h)[1].decode("utf-16-be", "replace")
        inner.append(tuple(sorted(set(re.findall(drp.GUID, data)))))
    assert len(dbids) == 2
    for ids in (dbids, uniques, inner):
        assert len(ids) == len(set(ids)), "идентификаторы записей совпали"
    assert all(ids for ids in inner), "в блобе не нашлось GUID — формат изменился?"


def test_дескриптор_описывает_свой_файл(tmp_path):
    """Запись медиапула — полный дескриптор медиа, а не ссылка: путь, имя,
    таймкод, число кадров и звук лежат в шести вложенных блобах. Клонировать
    запись и править только MediaExtents мало — внутри останется файл шаблона,
    и Resolve сочтёт две камеры одним медиа."""
    pytest.importorskip("zstandard")
    out = str(tmp_path / "d.drp")
    probes = {_p("m", "a", "A.MP4"): {"dur_s": 163.165, "timecode": "01;57;31;11",
                                       "width": 3840, "height": 2160},
              _p("m", "b", "B.MP4"): {"dur_s": 100.0, "timecode": "22;02;16;19",
                                       "width": 1920, "height": 1080}}
    drp.build(out, list(probes), [(0.0, 1.0)], [0.0, 0.0],
              probe=lambda p: dict(probes[p]))
    mp = drp.read(out)["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    got = {}
    for b in re.findall(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>", mp, re.S):
        clip = [h for h in re.findall(r"<Clip>([0-9a-f]{40,})</Clip>", b) if drp.is_zstd_blob(h)]
        d = drp.unpack_fields(clip[0])[1]
        t = bytes.fromhex(re.search(r"<Time>([0-9a-f]+)</Time>", b).group(1))
        got[drp.pb_get_str(d, 2)] = (
            drp.pb_get_str(d, 1),
            drp.kv_get(t, "Timecode").decode("utf-16-be"),
            int.from_bytes(drp.kv_get(t, "NumFrames"), "big"))
    assert got["A.MP4"] == (_p("m", "a"), "01:57:31:11", 4890)
    assert got["B.MP4"] == (_p("m", "b"), "22:02:16:19", 2997)


def test_дескрипторы_не_копируют_друг_друга(tmp_path):
    """Прямая ловушка на прошлый баг: у двух камер всё внутри должно различаться."""
    pytest.importorskip("zstandard")
    out = str(tmp_path / "d2.drp")
    probes = {_p("m", "a", "A.MP4"): dict(FAKE),
              _p("m", "b", "B.MP4"): {**FAKE, "timecode": "22;02;16;19"}}
    drp.build(out, list(probes), [(0.0, 1.0)], [0.0, 0.0], probe=lambda p: dict(probes[p]))
    mp = drp.read(out)["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    paths = []
    for b in re.findall(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>", mp, re.S):
        clip = [h for h in re.findall(r"<Clip>([0-9a-f]{40,})</Clip>", b) if drp.is_zstd_blob(h)]
        paths.append(tuple(drp.pb_get_str(drp.unpack_fields(c)[1], 1) for c in clip))
    assert len(paths) == 2 and paths[0] != paths[1]
    assert all(len(set(p)) == 1 for p in paths)      # видео и звук — из одной папки


def test_ссылка_на_звук_не_рвётся(built):
    """Ключ MediaRef внутри FieldsBlob — это DbId элемента <BtAudioInfo>, то есть
    звуковая часть записи. Если генерировать GUID в XML и в блобе порознь, ссылка
    рвётся: клипы на дорожке зелёные, но без волны и без звука."""
    _, _, files = built
    mp = files["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    checked = 0
    for b in re.findall(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>", mp, re.S):
        info = re.search(r'<BtAudioInfo DbId="(' + drp.GUID + r')"', b)
        blob = re.search(r"<FieldsBlob>([0-9a-f]{200,})</FieldsBlob>", b)
        assert info, "у записи пропал <BtAudioInfo>"
        ref = drp.kv_get(drp.unpack_fields(blob.group(1))[1], "MediaRef")
        assert ref.decode("utf-16-be") == info.group(1)
        checked += 1
    assert checked == 2


def test_mediaref_ведёт_в_медиапул(built):
    _, _, files = built
    seq = files[drp.seq_name(files)].decode("utf-8")
    mp = files["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    pool = set(re.findall(r'<Sm2MpVideoClip DbId="([0-9a-f-]+)"', mp))
    refs = set(re.findall(r"<MediaRef>([0-9a-f-]+)</MediaRef>", seq))
    assert refs and refs <= pool


def test_пути_в_клипах_таймлайна(built):
    """Полный путь Resolve хранит только у клипов таймлайна; запись медиапула
    несёт имя файла и метаданные, но не путь."""
    _, _, files = built
    seq = files[drp.seq_name(files)].decode("utf-8")
    paths = set(re.findall(r"<MediaFilePath>([^<]+)</MediaFilePath>", seq))
    assert paths == {_p("media", "a", "A.MP4"), _p("media", "b", "B.MP4")}


def test_в_медиапуле_длительность_из_probe(built):
    """Resolve берёт длительность отсюда, а не переспрашивает файл."""
    _, _, files = built
    mp = files["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    cams = 0
    for block in re.findall(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>", mp, re.S):
        for m in re.finditer(r"<FieldsBlob>([0-9a-f]{200,})</FieldsBlob>", block):
            if not drp.is_zstd_blob(m.group(1)):
                continue
            start, dur = drp.get_media_extents(m.group(1))
            assert dur == pytest.approx(163.165) and start == pytest.approx(7051.34, abs=0.05)
            cams += 1
    assert cams == 2                                   # по записи на камеру


def test_длительность_таймлайна_в_медиапуле(built):
    """Сам таймлайн тоже лежит в пуле и несёт свой хронометраж — если не поправить,
    там останется длительность шаблона."""
    _, info, files = built
    mp = files["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    block = re.search(r"<Sm2MpTimelineClip\b.*?</Sm2MpTimelineClip>", mp, re.S).group(0)
    h = re.search(r"<FieldsBlob>([0-9a-f]{200,})</FieldsBlob>", block).group(1)
    assert drp.get_media_extents(h) == pytest.approx((0.0, info["total_frames"] / 60))


def test_титры_несут_текст_и_цвет(built):
    """Жёлтым помечено слово №1 — у него должен быть цвет из HIGHLIGHT_SPEC."""
    _, _, files = built
    seq = files[drp.seq_name(files)].decode("utf-8")
    texts, colored = [], 0
    for h in re.findall(r"<CompositionBA>([0-9a-f]+)</CompositionBA>", seq):
        n = drp.split_comp(h)[2].decode("utf-8")
        texts.append(re.search(r'StyledText = Input \{ Value = "([^"]*)"', n).group(1))
        if "Green1 = Input { Value = 0.917600" in n:
            colored += 1
    assert texts == ["РАЗ", "ДВА", "ТРИ"] and colored == 1


def test_сдвиг_синхрона_учтён(built):
    """Камера 2 идёт со своим смещением: In у неё другой."""
    _, _, files = built
    seq = files[drp.seq_name(files)].decode("utf-8")
    vec = re.search(r"<VideoTrackVec>(.*?)</VideoTrackVec>", seq, re.S).group(1)
    t1, t2 = re.findall(r"<Element>\s*<Sm2TiTrack\b.*?</Sm2TiTrack>\s*</Element>", vec, re.S)[:2]
    ins = lambda t: [int(x) for x in re.findall(r"<In>(-?\d+)</In>", t)]
    assert ins(t2) == [i - 30 for i in ins(t1)]        # 0.5 с при 60 fps


def test_вставки_ложатся_на_свои_дорожки(tmp_path):
    pytest.importorskip("zstandard")
    out = str(tmp_path / "ins.drp")
    info = drp.build(out, [_p("media", "a", "A.MP4")], [(0.0, 2.0)], [0.0],
                     inserts=[{"type": "photo", "media": _p("media", "p.jpg"), "start": 0, "end": 30},
                              {"type": "video", "media": _p("media", "v.mp4"), "start": 30, "end": 60}],
                     probe=lambda p: dict(FAKE))
    assert info["inserts"] == 2
    files = drp.read(out)
    seq = files[drp.seq_name(files)].decode("utf-8")
    vec = re.search(r"<VideoTrackVec>(.*?)</VideoTrackVec>", seq, re.S).group(1)
    tracks = re.findall(r"<Element>\s*<Sm2TiTrack\b.*?</Sm2TiTrack>\s*</Element>", vec, re.S)
    assert len(tracks) == 3                            # камера + фото + видео
    assert "p.jpg" in tracks[1] and "v.mp4" in tracks[2]


def test_фото_запись_без_звука_и_с_плоским_полями(tmp_path):
    """Фото-запись отличается от камерной: нет <BtAudioInfo>/<TracksBA>, FieldsBlob
    у неё НЕ zstd (там лежит только служебная шапка), в <Time> нет Timecode —
    иначе Resolve падал бы на чужой структуре. Снято с реального экспорта."""
    pytest.importorskip("zstandard")
    out = str(tmp_path / "photo.drp")
    drp.build(out, [_p("media", "a", "A.MP4")], [(0.0, 2.0)], [0.0],
              inserts=[{"type": "photo", "media": _p("media", "p.jpg"), "start": 0, "end": 30}],
              probe=lambda p: dict(FAKE))
    mp = drp.read(out)["MediaPool/Master/MpFolder.xml"].decode("utf-8")
    blocks = re.findall(r"<Sm2MpVideoClip\b.*?</Sm2MpVideoClip>", mp, re.S)
    assert len(blocks) == 2                            # камера + фото
    photo = next(b for b in blocks if "A.MP4" not in b)
    assert "<BtAudioInfo" not in photo and "<TracksBA>" not in photo
    fb = re.search(r"<FieldsBlob>([0-9a-f]{40,})</FieldsBlob>", photo).group(1)
    assert not drp.is_zstd_blob(fb)
    t = bytes.fromhex(re.search(r"<Time>([0-9a-f]+)</Time>", photo).group(1))
    assert drp.kv_get(t, "Timecode") is None           # у фото вместо него StartFrame
    assert int.from_bytes(drp.kv_get(t, "NumFrames"), "big") == 1


def test_фото_клип_несёт_контейнерную_карту(tmp_path):
    """MediaTimemapBA у фото-клика — контейнер ключей (YMin/YMax/XMax/KeyframesBA),
    а не 5 doubles, как у медиаклика камеры. Шаблонный контейнер править не надо:
    build() его не трогает."""
    pytest.importorskip("zstandard")
    out = str(tmp_path / "ph.drp")
    drp.build(out, [_p("media", "a", "A.MP4")], [(0.0, 2.0)], [0.0],
              inserts=[{"type": "photo", "media": _p("media", "p.jpg"), "start": 0, "end": 30}],
              probe=lambda p: dict(FAKE))
    files = drp.read(out)
    seq = files[drp.seq_name(files)].decode("utf-8")
    maps = [m for m in re.findall(r"<MediaTimemapBA>([0-9a-f]{40,})</MediaTimemapBA>", seq)]
    # камерные карты — 5 doubles (тип 02); фото несёт контейнер ключей (тип 01)
    photo_map = next(h for h in maps if h[:2] != "02")
    assert "KeyframesBA".encode("utf-16-be") in bytes.fromhex(photo_map)


def test_новые_dbid_уникальны():
    el = '<A DbId="11111111-1111-1111-1111-111111111111"><B DbId="11111111-1111-1111-1111-111111111111"/></A>'
    ids = re.findall(r'DbId="([0-9a-f-]+)"', drp.new_ids(el))
    assert len(ids) == 2 and len(set(ids)) == 2
