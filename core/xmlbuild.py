# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Build a Premiere-compatible FCP7 xmeml v4 timeline:
multicam cut (cam1=V1 bottom always-on, cam2=V2 top enabled on its segments),
two audio tracks (one per camera), silences already removed.
Subtitles (3rd video track) are added separately by the subtitle stage.

Timeline model (validated against the real Timeline 2.xml):
  * sequence rate 60fps; all start/end/in/out are 60fps frames.
  * source position seconds -> in/out frames = round(sec * 60).
  * pproTicks = frame * 4233600000.
  * cam2 source time = cam1 source time + delta, delta = -sync_offset.
"""
import os, re, urllib.parse, subprocess, json
from core import fileio
from core.xmltext import xml_text as _esc

FPS = 60
TICKS_PER_FRAME = 4233600000          # ppro ticks per 60fps frame
SUB_FIT_CHARS = 14                    # words longer than this get font-scaled down to fit
SRC_FPS = 30000 / 1001                # 29.97 source rate (display only)


def pathurl(p):
    """Путь ОС -> file://-URL с процентным экранированием.

    Кодируем БАЙТЫ пути (`os.fsencode`), а не строку: `quote(str)` падал
    `UnicodeEncodeError` на имени с нечитаемым байтом (на Linux `os.listdir` отдаёт
    его одиночным суррогатом `\\udcff`), и падала вся сборка XML. Нижний регистр
    `%xx` — как было: эталонные XML не должны меняться."""
    p = os.path.abspath(p).replace("\\", "/")
    q = urllib.parse.quote_from_bytes(os.fsencode(p), safe="/")
    q = re.sub(r"%[0-9A-Fa-f]{2}", lambda m: m.group(0).lower(), q)
    return "file://localhost/" + q


def unpathurl(u):
    """file://localhost/C%3a/… -> C:\\… (обратная к pathurl).

    Разделитель — os.sep, а не жёсткий бэкслэш: на Linux и macOS «/tmp/a/cam.mp4»
    превращался в «\\tmp\\a\\cam.mp4», то есть в несуществующий путь, и любой путь
    из XML там «пропадал». На Windows os.sep и есть «\\» — поведение прежнее.

    Раскодированные БАЙТЫ отдаём через `os.fsdecode`: `unquote` декодировал их как
    UTF-8 с `errors="replace"`, и имя с нечитаемым байтом (`%ff`) превращалось в
    «\\ufffd» — файл, которого нет (пара к `pathurl`, IB, п. 3).
    (Поймано 2026-08-14: предполёт рендера на Linux-CI объявлял камеры пропавшими.)
    """
    raw = urllib.parse.unquote_to_bytes(
        u.replace("file://localhost/", "").replace("file:///", ""))
    return os.fsdecode(raw).replace("/", os.sep)


# <file>…</file> целиком: вложенных <file> внутри не бывает, потому нежадно до первого закрытия
_FILE_BLOCK = re.compile(r'<file\s+id="[^"]+"\s*>.*?</file>', re.S)
_TC_STRING = re.compile(r'(<timecode>.*?<string>)[^<]*(</string>)', re.S)


def fix_timecodes(text):
    """Проставить в ГОТОВОМ XML настоящие таймкоды исходников. Возвращает (текст, сколько).

    Нужно для файлов, нарезанных до того, как починили `probe()` (см. pick_timecode):
    в них лежат нули, и DaVinci Resolve, который позиционирует клипы по таймкоду,
    раскладывает нарезку со сдвигом в часы. Правим на лету при скачивании, файл на
    диске не трогаем — он рабочий, его читают редактор, субтитры и `xml2ae`.

    Трогаем только блоки с `<pathurl>`: у субтитр-графики (`GraphicAndType`) пути нет
    и таймкод у неё синтетический. Пропавшее медиа — не ошибка: оставляем как было.
    """
    n = 0

    def one(m):
        nonlocal n
        block = m.group(0)
        pu = re.search(r"<pathurl>([^<]+)</pathurl>", block)
        if not pu:
            return block
        path = unpathurl(pu.group(1))
        if not os.path.isfile(path):
            return block
        try:
            tc = probe(path)["timecode"]
        except Exception:
            return block                       # битый контейнер — не повод ронять скачивание
        fixed = _TC_STRING.sub(lambda t: t.group(1) + tc + t.group(2), block, count=1)
        if fixed != block:
            n += 1
        return fixed

    return _FILE_BLOCK.sub(one, text), n


_PROBE_CACHE = {}                     # (path, mtime) -> dict — в батче камера пробуется 1 раз


def pick_timecode(d):
    """Стартовый таймкод исходника из ЛЮБОГО потока или из формата.

    Раньше спрашивали только у `v:0`, но камеры Sony (XAVC) кладут таймкод в
    служебную дорожку (`tmcd`/`rtmd`), а не в теги видеопотока — и в XML вместо
    настоящего таймкода уезжали нули. Премьеру всё равно: он берёт медиа по
    `pathurl` и опирается на pproTicks. А DaVinci Resolve позиционирует клипы
    ПО ТАЙМКОДУ, и от нулей вся нарезка ехала на часы (файл начинается с
    01;57;31;11, а мы обещали 00;00;00;00).

    Точка с запятой = drop-frame, ровно так это пишет и сам Премьер: сверено с
    его экспортом того же таймлайна, там `01;57;31;11` при ffprobe `01:57:31:11`.
    """
    for s in (d.get("streams") or []) + [d.get("format") or {}]:
        tc = (s.get("tags") or {}).get("timecode")
        if tc:
            return tc.replace(":", ";")
    return "00;00;00;00"


def probe(path, still_ok=True):
    """Return dict(width,height,dur_s,timecode,fps?).

    `still_ok` — разрешить фото: у картинок ffprobe не даёт `format.duration`
    (PNG) или даёт чепуху 0.04 с (JPG), длительность им не нужна вовсе. Для
    фаз, где фото бывают (сборка .drp), это не ошибка, а `dur_s = 0`.
    `fps` — реальная частота видеопотока (`avg_frame_rate`): 25.0 для PAL-камер,
    29.97 для NTSC. По ней считается таймкод-математика в drp.py.
    """
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        key = None
    if key and key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    # без belium_streams: таймкод может лежать в служебной дорожке (см. pick_timecode)
    out = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "stream=width,height,codec_type,avg_frame_rate"
                          ":stream_tags=timecode:format=duration:format_tags=timecode",
         "-of", "json", path], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout
    d = json.loads(out)
    streams = d.get("streams") or []
    st = next((s for s in streams if s.get("codec_type") == "video"), None)
    if st is None:
        raise RuntimeError(f"ffprobe не нашёл видеопоток: {os.path.basename(path)}")
    tc = pick_timecode(d)
    try:
        dur_s = float(d["format"]["duration"])
    except (KeyError, ValueError, TypeError):
        if not still_ok:
            raise RuntimeError(f"ffprobe не вернул длительность: {os.path.basename(path)} "
                               f"(битый контейнер или duration=N/A)")
        dur_s = 0.0                                  # фото: длительности нет, и не нужна
    res = {"width": int(st["width"]), "height": int(st["height"]),
           "dur_s": dur_s, "timecode": tc}
    ar = (st.get("avg_frame_rate") or "").strip()
    if ar and "/" in ar:                        # "25/1", "30000/1001"
        n, d = ar.split("/", 1)
        try:
            res["fps"] = float(n) / float(d)
        except (ValueError, ZeroDivisionError):
            pass
    if key:
        _PROBE_CACHE[key] = res
    return res


# ---- filter snippets -------------------------------------------------------
VIDEO_FILTERS = """\t\t\t\t\t\t<filter>
\t\t\t\t\t\t\t<effect>
\t\t\t\t\t\t\t\t<name>Basic Motion</name>
\t\t\t\t\t\t\t\t<effectid>basic</effectid>
\t\t\t\t\t\t\t\t<effectcategory>motion</effectcategory>
\t\t\t\t\t\t\t\t<effecttype>motion</effecttype>
\t\t\t\t\t\t\t\t<mediatype>video</mediatype>
\t\t\t\t\t\t\t\t<pproBypass>false</pproBypass>
\t\t\t\t\t\t\t\t<parameter authoringApp="PremierePro">
\t\t\t\t\t\t\t\t\t<parameterid>scale</parameterid>
\t\t\t\t\t\t\t\t\t<name>Scale</name>
\t\t\t\t\t\t\t\t\t<valuemin>0</valuemin>
\t\t\t\t\t\t\t\t\t<valuemax>1000</valuemax>
\t\t\t\t\t\t\t\t\t<value>{scale}</value>
\t\t\t\t\t\t\t\t</parameter>
\t\t\t\t\t\t\t\t<parameter authoringApp="PremierePro">
\t\t\t\t\t\t\t\t\t<parameterid>center</parameterid>
\t\t\t\t\t\t\t\t\t<name>Center</name>
\t\t\t\t\t\t\t\t\t<value><horiz>0</horiz><vert>0</vert></value>
\t\t\t\t\t\t\t\t</parameter>
\t\t\t\t\t\t\t</effect>
\t\t\t\t\t\t</filter>
"""

AUDIO_FILTER = """\t\t\t\t\t\t<filter>
\t\t\t\t\t\t\t<effect>
\t\t\t\t\t\t\t\t<name>Audio Levels</name>
\t\t\t\t\t\t\t\t<effectid>audiolevels</effectid>
\t\t\t\t\t\t\t\t<effectcategory>audiolevels</effectcategory>
\t\t\t\t\t\t\t\t<effecttype>audiolevels</effecttype>
\t\t\t\t\t\t\t\t<mediatype>audio</mediatype>
\t\t\t\t\t\t\t\t<pproBypass>false</pproBypass>
\t\t\t\t\t\t\t\t<parameter authoringApp="PremierePro">
\t\t\t\t\t\t\t\t\t<parameterid>level</parameterid>
\t\t\t\t\t\t\t\t\t<name>Level</name>
\t\t\t\t\t\t\t\t\t<valuemin>0</valuemin>
\t\t\t\t\t\t\t\t\t<valuemax>3.98109</valuemax>
\t\t\t\t\t\t\t\t\t<value>__LEVEL__</value>
\t\t\t\t\t\t\t\t</parameter>
\t\t\t\t\t\t\t</effect>
\t\t\t\t\t\t</filter>
"""


def _db_to_gain(db):
    return round(10 ** (db / 20.0), 6)


def _file_def(file_id, name, url, dur_s, width, height, tc):
    src_dur = round(dur_s * SRC_FPS)
    return f"""\t\t\t\t\t\t<file id="{file_id}">
\t\t\t\t\t\t\t<name>{_esc(name)}</name>
\t\t\t\t\t\t\t<pathurl>{url}</pathurl>
\t\t\t\t\t\t\t<rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>
\t\t\t\t\t\t\t<duration>{src_dur}</duration>
\t\t\t\t\t\t\t<timecode><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><string>{tc}</string><displayformat>DF</displayformat></timecode>
\t\t\t\t\t\t\t<media>
\t\t\t\t\t\t\t\t<video><samplecharacteristics><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><width>{width}</width><height>{height}</height><anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance></samplecharacteristics></video>
\t\t\t\t\t\t\t\t<audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics><channelcount>2</channelcount></audio>
\t\t\t\t\t\t\t</media>
\t\t\t\t\t\t</file>
"""


def _video_clip(cid, mcid, name, enabled, dur_frames, start, end, tin, tout,
                file_xml, scale):
    return f"""\t\t\t\t\t<clipitem id="clipitem-{cid}">
\t\t\t\t\t\t<masterclipid>masterclip-{mcid}</masterclipid>
\t\t\t\t\t\t<name>{_esc(name)}</name>
\t\t\t\t\t\t<enabled>{"TRUE" if enabled else "FALSE"}</enabled>
\t\t\t\t\t\t<duration>{dur_frames}</duration>
\t\t\t\t\t\t<rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>
\t\t\t\t\t\t<start>{start}</start>
\t\t\t\t\t\t<end>{end}</end>
\t\t\t\t\t\t<in>{tin}</in>
\t\t\t\t\t\t<out>{tout}</out>
\t\t\t\t\t\t<pproTicksIn>{tin*TICKS_PER_FRAME}</pproTicksIn>
\t\t\t\t\t\t<pproTicksOut>{tout*TICKS_PER_FRAME}</pproTicksOut>
\t\t\t\t\t\t<alphatype>none</alphatype>
\t\t\t\t\t\t<pixelaspectratio>square</pixelaspectratio>
\t\t\t\t\t\t<anamorphic>FALSE</anamorphic>
{file_xml}{VIDEO_FILTERS.format(scale=scale)}\t\t\t\t\t</clipitem>
"""


def _audio_clip(cid, mcid, name, dur_frames, start, end, tin, tout, file_ref,
                trackindex=1, level=1.0):
    return f"""\t\t\t\t\t<clipitem id="clipitem-{cid}" premiereChannelType="mono">
\t\t\t\t\t\t<masterclipid>masterclip-{mcid}</masterclipid>
\t\t\t\t\t\t<name>{_esc(name)}</name>
\t\t\t\t\t\t<enabled>TRUE</enabled>
\t\t\t\t\t\t<duration>{dur_frames}</duration>
\t\t\t\t\t\t<rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>
\t\t\t\t\t\t<start>{start}</start>
\t\t\t\t\t\t<end>{end}</end>
\t\t\t\t\t\t<in>{tin}</in>
\t\t\t\t\t\t<out>{tout}</out>
\t\t\t\t\t\t<pproTicksIn>{tin*TICKS_PER_FRAME}</pproTicksIn>
\t\t\t\t\t\t<pproTicksOut>{tout*TICKS_PER_FRAME}</pproTicksOut>
\t\t\t\t\t\t{file_ref}
\t\t\t\t\t\t<sourcetrack><mediatype>audio</mediatype><trackindex>{trackindex}</trackindex></sourcetrack>
{AUDIO_FILTER.replace("__LEVEL__", str(level))}\t\t\t\t\t</clipitem>
"""


def probe_audio_dur(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _music_file_def(file_id, name, url, dur_s):
    return f"""\t\t\t\t\t\t<file id="{file_id}">
\t\t\t\t\t\t\t<name>{_esc(name)}</name>
\t\t\t\t\t\t\t<pathurl>{url}</pathurl>
\t\t\t\t\t\t\t<rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>
\t\t\t\t\t\t\t<duration>{round(dur_s*FPS)}</duration>
\t\t\t\t\t\t\t<media><audio><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics><channelcount>2</channelcount></audio></media>
\t\t\t\t\t\t</file>"""


def _vtrack(clips_xml, targeted):
    return (f'\t\t\t\t<track TL.SQTrackShy="0" TL.SQTrackExpandedHeight="41" '
            f'TL.SQTrackExpanded="0" MZ.TrackTargeted="{targeted}">\n'
            f"{clips_xml}\t\t\t\t\t<enabled>TRUE</enabled>\n\t\t\t\t\t<locked>FALSE</locked>\n\t\t\t\t</track>\n")


def clean_sub_text(w, upper=True):
    """Drop dashes (speech dashes slip in), strip surrounding punctuation, censor
    TikTok-unsafe words, uppercase to match the project style. '' for punct-only."""
    for d in ("—", "–", "―", "−"):           # em/en/horizontal/minus dashes -> space
        w = w.replace(d, " ")
    w = " ".join(w.split())                    # collapse whitespace
    w = w.strip(".,!?;:…«»\"'()[]-‐‑ ").strip()
    if w:
        from core import censor
        w = censor.censor(w)
    return w.upper() if upper else w


def _atrack(clips_xml, outidx):
    return (f'\t\t\t\t<track TL.SQTrackAudioKeyframeStyle="0" TL.SQTrackShy="0" '
            f'TL.SQTrackExpandedHeight="41" TL.SQTrackExpanded="0" MZ.TrackTargeted="1" '
            f'PannerCurrentValue="0.5" PannerName="Balance" currentExplodedTrackIndex="0" '
            f'totalExplodedTrackCount="1" premiereTrackType="Mono">\n'
            f"{clips_xml}\t\t\t\t\t<enabled>TRUE</enabled>\n\t\t\t\t\t<locked>FALSE</locked>\n"
            f"\t\t\t\t\t<outputchannelindex>{outidx}</outputchannelindex>\n\t\t\t\t</track>\n")

def build_subtitle_track(sub_words, start_id=1):
    """Сборка видеодорожки с клипами субтитров для Premiere XML.

    sub_words: список словарей {'w': text, 'start': frame, 'end': frame}
    start_id: начальный числовой id клипа (clipitem-id)

    Возвращает (vtrack_xml, n_subs, long_words, next_id).
    Слова длиннее SUB_FIT_CHARS масштабируются (scale < 100.0),
    не влезающие в шаблон даже с масштабом попадают в long_words.
    """
    v3track = ""
    n_subs = 0
    long_words = []
    cid = start_id
    if sub_words:
        from core.subs import SubtitleBuilder
        sb = SubtitleBuilder()
        v3 = ""
        for wd in sub_words:
            st, en = int(wd["start"]), int(wd["end"])
            text = clean_sub_text(wd["w"])
            if en <= st or not text:
                continue
            chars = len(text)
            scale = 100.0 if chars <= SUB_FIT_CHARS else round(SUB_FIT_CHARS / chars * 100, 1)
            try:
                v3 += sb.clip(text, st, en, cid, cid, scale=scale); cid += 1
                n_subs += 1
            except ValueError:
                long_words.append(text)   # too long for a template -> skip, report
        v3track = _vtrack(v3, 0)
    return v3track, n_subs, long_words, cid


def build(cam_paths, segments, offsets, out_path, assign=None,
          seq_w=1080, seq_h=1920, scale=50.4, sub_words=None, name=None,
          music_path=None, music_db=-20.0):
    """Multicam timeline for N cameras (N = len(cam_paths), 1..4).
    cam_paths: camera files, camera 1 first (the base). offsets: per-camera sync
    offset in seconds (offsets[0]=0; offsets[k]=find_offset(cam1,camk)). assign:
    active-camera index (0-based) per KEPT segment (None -> all camera 1).
    Camera 1 is always enabled (base); camera k>0 is enabled only where assign==k."""
    if isinstance(cam_paths, str):        # back-compat: single path
        cam_paths = [cam_paths]
    N = len(cam_paths)
    if assign is None:
        assign = [0] * len(segments)
    probes = [probe(p) for p in cam_paths]
    names = [os.path.basename(p) for p in cam_paths]
    urls = [pathurl(p) for p in cam_paths]
    durfs = [round(pr["dur_s"] * FPS) for pr in probes]
    seq_name = _esc(name or os.path.splitext(names[0])[0])

    vclips = ["" for _ in range(N)]
    aclips = ["" for _ in range(N)]
    fdefined = [False] * N
    cid = 100
    tl = 0                                # timeline cursor (frames)
    kept = 0                              # index into assign (per kept segment)
    for s, e in segments:
        in0, out0 = round(s * FPS), round(e * FPS)
        length = out0 - in0
        if length <= 0:
            continue
        start, end = tl, tl + length
        active = assign[kept] if kept < len(assign) else 0
        # Камера, назначенная куску, могла ещё не начать писать (кусок начинается раньше
        # её сдвига): кусок берём с камеры 1 — её сдвиг 0, и материал там есть всегда.
        # max(0, …) тут нельзя: он показал бы кадры не из того места и сломал синхрон.
        if active and s < offsets[active]:
            active = 0
        for k in range(N):
            if k and s < offsets[k]:
                # У камеры k в этот момент материала нет вовсе (её файл начинается позже):
                # клип не пишем. Иначе <in> уходит отрицательным — и у видео, и у аудио,
                # которое в XML включено всегда. Тишина честнее чужих кадров.
                continue
            ink = round((s - offsets[k]) * FPS)   # camk_time = cam1_time - offset_k
            outk = ink + length
            if not fdefined[k]:
                fx = ("\t\t\t\t\t\t" + _file_def(f"file-{k+1}", names[k], urls[k],
                      probes[k]["dur_s"], probes[k]["width"], probes[k]["height"],
                      probes[k]["timecode"]).strip() + "\n")
                fdefined[k] = True
            else:
                fx = f'\t\t\t\t\t\t<file id="file-{k+1}"/>\n'
            enabled = (k == 0) or (active == k)   # cam1 base always on; others on their cuts
            vclips[k] += _video_clip(cid, k+1, names[k], enabled, durfs[k], start, end,
                                     ink, outk, fx, scale); cid += 1
            aclips[k] += _audio_clip(cid, k+1, names[k], durfs[k], start, end, ink, outk,
                                     f'<file id="file-{k+1}"/>'); cid += 1
        tl = end
        kept += 1
    total = tl

    v3track, n_subs, long_words, cid = build_subtitle_track(sub_words, cid)

    # optional music bed (downloaded track) on two audio tracks (L/R)
    music_tracks = ""
    if music_path and os.path.isfile(music_path):
        mdur = probe_audio_dur(music_path)
        mname = os.path.basename(music_path)
        mlen = round(mdur * FPS)
        if total:
            mlen = min(mlen, total) or mlen
        mdef = _music_file_def("file-music", mname, pathurl(music_path), mdur)
        gain = _db_to_gain(music_db)
        mL = _audio_clip(cid, "music", mname, round(mdur * FPS), 0, mlen, 0, mlen,
                         "\t\t\t\t\t\t" + mdef.strip() + "\n", trackindex=1, level=gain); cid += 1
        mR = _audio_clip(cid, "music", mname, round(mdur * FPS), 0, mlen, 0, mlen,
                         '<file id="file-music"/>', trackindex=2, level=gain); cid += 1
        music_tracks = _atrack(mL, N + 1) + _atrack(mR, N + 2)

    seq = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
\t<sequence id="sequence-1" MZ.Sequence.PreviewFrameSizeHeight="{seq_h}" MZ.Sequence.PreviewFrameSizeWidth="{seq_w}" explodedTracks="true">
\t\t<uuid>00000000-0000-0000-0000-000000000001</uuid>
\t\t<duration>{total}</duration>
\t\t<rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate>
\t\t<name>{seq_name}</name>
\t\t<media>
\t\t\t<video>
\t\t\t\t<format><samplecharacteristics><rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate><width>{seq_w}</width><height>{seq_h}</height><anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio><fielddominance>none</fielddominance><colordepth>24</colordepth></samplecharacteristics></format>
{_vtrack(vclips[0], 1)}{"".join(_vtrack(vclips[k], 0) for k in range(1, N))}{v3track}\t\t\t</video>
\t\t\t<audio>
\t\t\t\t<numOutputChannels>2</numOutputChannels>
\t\t\t\t<format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>
{"".join(_atrack(aclips[k], k + 1) for k in range(N))}{music_tracks}\t\t\t</audio>
\t\t</media>
\t\t<timecode><rate><timebase>60</timebase><ntsc>FALSE</ntsc></rate><string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>
\t</sequence>
</xmeml>
"""
    fileio.atomic_text_write(out_path, seq, encoding="UTF-8")
    return {"segments": len([1 for s, e in segments if round(e*FPS) > round(s*FPS)]),
            "total_frames": total, "total_s": total / FPS, "cameras": N,
            "subtitles": n_subs, "long_words": long_words}
