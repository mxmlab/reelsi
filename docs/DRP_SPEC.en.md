# Export to DaVinci Resolve — the `.drp` format

Russian version: [docs/DRP_SPEC.md](DRP_SPEC.md).

Status: generating `.drp` from scratch works — cameras, cutting, sync, inserts,
titles, UI. Figured out on DaVinci Resolve Studio 21.0.3.

## Why `.drp` at all

Graphics (titles) **cannot** be transferred by any interchange format. This is not a gap
in the implementation but how the formats are built: they standardise the edit, not the drawing.

| format | title text |
|---|---|
| FCP7 XML (`xmeml`) — what we hand over | **no** |
| FCPXML (Final Cut X) | no — "titles in Final Cut's proprietary format" |
| AAF, EDL, ADL, OTIO | no |
| `.srt` / `.vtt` / `.ttml` | yes, but that is a subtitle track, not graphics |
| `.drp` / `.drt` — Resolve's native format | **yes** |

Verified experimentally, not read somewhere: the user made a Fusion title in
Resolve and exported it to FCP7 XML — in place of the title there was an empty `Slug`,
and the whole file had zero occurrences of `Text`, `font`, `color` and not a single Cyrillic
letter. Resolve does not carry even its own title through that format.

In Premiere the title text lives in a proprietary FlatBuffer blob (668 characters
of base64 per word, see `data/sub_template.xml`) — only Premiere can read it.
Both editors hid the text in their own place, because there is no common place for it.

## The `.drp` format — fully figured out

Taken from an export of **DaVinci Resolve Studio 21.0.3.0007**. The format is private and
undocumented: when the Resolve version changes, verify it again.

```
.drp                                ZIP
 ├ project.xml
 ├ MediaPool/Master/MpFolder.xml    media pool records
 ├ SeqContainer/<uuid>.xml          timeline: tracks, clips, timings
 └ Gallery.xml
```

The time and attributes of the archive entries are pinned (`core/drp.py`: `ZIP_DATE_TIME` =
1980-01-01 00:00, `ZIP_CREATE_SYSTEM` = 0, `ZIP_EXTERNAL_ATTR` = `0o600 << 16` —
as in the reference Resolve export). The reason: `.drp` is an EXPORT, not a project with a
history of edits, and the real build time (the DOS time of the local header, a step of
2 s) made two identical exports different — they diverged already at byte 10, and
byte-for-byte comparison of builds floated on that. Whether Resolve needs the real
time is unknown: it does not appear once in the observations below, and the composition of
the archive does not depend on it. If after a Resolve update the project stops being
importable, this is the first thing worth rechecking.

### The timeline

Ordinary XML. A track = `<Sm2TiTrack>`, a clip = `<Sm2TiVideoClip DbId="…">`.
The clip's significant fields: `Name`, `Start`, `Duration`, `In`, `MediaFilePath`,
`MediaRef` (→ the `DbId` of the media pool record), `MediaTimemapBA`.

`MediaTimemapBA` = `02` + 8 bytes of big-endian double = **duration in seconds**
(frames / 60). The re-encoding was verified byte for byte.

### The Fusion title — self-contained

The title clip has `PrettyType` = `Fusion Composition`, no `MediaFilePath` and no
`MediaRef`. All the content is in `<CompositionBA>`:

```
<CompositionBA>  hex
 └ uint32 BE (unpacked length) + zlib
    └ binary container: UTF-16BE keys with a 4-byte length prefix
       key "0_data": [4 bytes type][1 byte][4 bytes BE DATA LENGTH][data]
       data = composition text … "Compressed = true, }\0"
                + uint32 LE (length) + zlib(node graph)
```

**The "data length" field is the main trap.** The first attempt at editing gave a black
layer precisely because the data size changed while the field stayed old. Check
yourself with an identity repack: parse and reassemble without edits → the unpacked
bytes must match byte for byte. Without this check the error is invisible.

The node graph is ordinary Fusion text:

```lua
{ Text1 = TextPlus { Inputs = {
     Width = Input { Value = 1080 }, Height = Input { Value = 1920 },
     StyledText = Input { Value = "СЛОВО" },
     Font = Input { Value = "Open Sans" }, Style = Input { Value = "Bold" },
     Size = Input { Value = 0.0729 },
     Red1 = …, Green1 = …, Blue1 = …,
     Center = Input { Value = { 0.5, 0.4036 } },
     VerticalJustificationNew = Input { Value = 3 },
     HorizontalJustificationNew = Input { Value = 3 } } },
  MediaOut1 = MediaOut { Inputs = { Input = Input { SourceOp = "Text1", Source = "Output" } } } }
```

Fusion **does not serialise default values** — if we set a colour or a point size,
the inputs must be added, not merely replaced. Y is counted bottom-up: the line anchor
from `docs/HIGHLIGHT_SPEC.md` (`Y = 0.5964·H` from the top) gives `Center.y = 1 − 0.5964`.

### The media pool — figured out and written

A record = `<Sm2MpVideoClip DbId="…">`, and the timeline clip's `MediaRef` points at its
`DbId`. Its `FieldsBlob`:

```
[4 bytes][4 bytes = length − 8][1 marker byte] + zstd   (the zstd magic from byte 9)
 └ protobuf wrapper (field 1 → field 1 → field 5)
    └ UTF-16BE key container, as in the composition:
       [4 name length][name][4 type][1 flag][4 length][data]

       Type          type 3   int32
       MediaExtents  type 12  16 bytes = two doubles LE
       CodecName     type 10  string
       ChannelVecBA  type 12  nested container (audio channels)
       MediaRef      type 10  GUID
       BitDepth      type 3
```

**`MediaExtents` = [start timecode in seconds, duration in seconds].**
For `C1412.MP4` it holds 7051.344 — which is exactly the drop-frame timecode `01;57;31;11`.
That is, the same quantity because of which the timings drifted in XML: the data source for
both formats is the same, `xmlbuild.probe()`.

Verified on a live Resolve:

- **The blob is mandatory.** Erase `FieldsBlob` — the project does not import at all,
  there is no timeline. The hope that Resolve would re-ask for the files itself did not come true.
- **Repacking zstd is safe.** Decompress and compress again (level 19) — the project
  opens, the media is online, the titles are intact. So the zstd stream is ordinary, without a dictionary.
- **Resolve reads the duration from the blob, not from the file.** A project
  retargeted from `C1412.MP4` (163 s) to `C1413.MP4` (204 s) shows 204 s —
  so `MediaExtents` must be computed honestly.
- **Patch precisely.** `MediaExtents` exists both on the second camera and on the
  timeline itself: editing "all blobs at once" breaks the neighbours. Find the needed record by
  `DbId` / `Name`.

`zstd` is needed: it is in the stdlib from Python 3.14, and we are on 3.10 → the `zstandard` package.

### A media pool record is a FULL DESCRIPTOR, not a reference

The most expensive discovery. Besides `FieldsBlob` a record has five more nested blobs, and
all of them describe a specific file:

| blob | what it carries | how to edit |
|---|---|---|
| `<Clip>` ×2 | path, name, mtime, codec — video and audio separately | protobuf, lengths recomputed |
| `<Time>` | `Timecode`, `NumFrames`, `FrameRate` | in place |
| `<Geometry>` | `Resolution` | in place |
| `<TracksBA>` | `StartTime`, `Duration` (in samples), `SampleRate`, `NumChannels` | in place |
| `FieldsBlob` | `MediaExtents`, `CodecName`, `MediaRef` | in place |

Cloning a record and fixing only `MediaExtents` is NOT ENOUGH: the template's file will remain
inside, and Resolve will consider the two cameras one piece of media — the first goes
offline, the second shows a frozen frame.

Almost everything is of fixed size, because the timecode is always `HH:MM:SS:FF` (11
characters) and the numbers are 4 or 8 bytes. Only the paths in `<Clip>` are variable-length.

The key container: `[4 name length][name UTF-16BE][4 type][1 flag][value]`.
For types 10 (string) and 12 (block) there are another 4 length bytes before the value. **The flag
byte after the type is easy to overlook** — without it every scalar is read shifted by a byte.

### A record's audio — a separate descriptor: the processed voice of camera 1

A camera record has two `<Clip>`s: video and audio. The audio one lies inside `<BtAudioInfo>`
(this is the `<EmbeddedAudioVec>` block), the video one inside `<BtVideoInfo>`. The audio
`<Clip>` has no field 6 (the name repeat) and no GUID, and the codec in field 5 is `Linear PCM`.
Next to it is its own `<TracksBA>`: `StartTime`, `SampleRate`, `NumChannels`, `Duration`
(in samples), `CodecName`, `BitDepth`. This is how Resolve stores "attached audio" from an
external recorder too: audio is a separate descriptor in the same media pool record.

That is why the processed voice of camera 1 (`<stem>.voice.<key8>.wav` with the `.voice.json`
sidecar, the version name is resolved by `core/voicefx.py:final_voice_path`; for clips baked
before versioned names, the old `<stem>.voice.wav`) travels into `.drp` by replacing
exactly this descriptor (`core/drp.py`, `set_voice_descriptor`):

| field | where the value comes from |
|---|---|
| audio `<Clip>`, fields 1–3 | the voice's folder, file name and mtime |
| audio `<Clip>`, field 5 | `Linear PCM` — the WAV codec, the same as in the template |
| `<TracksBA>`, `SampleRate`, `NumChannels` | from the WAV itself (the `wave` module, without ffprobe) |
| `<TracksBA>`, `Duration` | WAV duration × sample rate, in samples |

What stays the camera here, and why:

- **video** — its own `<Clip>`, `<Time>` and `<Geometry>` are not touched: the picture is from the camera;
- **`StartTime` in `<TracksBA>`** — the camera's (the source's timecode). This is the record's
  time scale, and the timeline clips take their `In`/`MediaStartTime` from it: the voice is computed
  from the sound of camera 1 on the same scale, so the clip timings do not change;
- **`MediaRef` in `FieldsBlob`** — still the `DbId` of the `<BtAudioInfo>` element:
  the identifiers are not regenerated on replacement, the reference is intact;
- **the record's `<Name>`** — the camera file name: for Resolve the record remains a camera.

If there is no file next to the XML (or it does not read as WAV) — there is no replacement at all, and
`.drp` stays as it was, byte for byte. Only the record of CAMERA 1 changes: the other cameras
have their own sound.

**What is missing here.** The record's `<AudioSource>` remains `AUDIO_SOURCE_EMBEDDED`
from the template: there is no other value in the reference (it was taken from an export where the sound lay in
the camera file itself), and inventing it blindly is not allowed. Whether such a record opens in
Resolve — the tests do not show, it is verified by importing `.drp` by hand (see
`docs/KNOWN_ISSUES.md`).

### Identifiers: one table for the whole record

The blobs REFERENCE XML elements. The `MediaRef` key in `FieldsBlob` is the `DbId`
of the `<BtAudioInfo>` element, that is, the record's audio. Therefore, when cloning, identifiers must not
be generated separately in XML and in the blobs: ONE old → new table is needed,
applied everywhere. Otherwise the reference breaks.

The symptoms this gave (caught in three passes, each time only the place somebody
looked at was fixed):

- `DbId` collided → the first camera offline, the second with a frozen frame;
- `UniqueMediaPoolItemId` collided → the same thing;
- `MediaRef` diverged from `BtAudioInfo` → the clips are green, but **without a waveform and without sound**.

### A timeline clip: two traps

**A media clip's `MediaTimemapBA` is not the clip's duration.** It is the SOURCE's map,
five doubles `[L, 0, L+1/60, 0, L]`, where `L = (frames−1)/30`, the same for all
clips of one camera. The clip's duration (one double) exists only on Fusion titles.
Mixing them up = a frozen picture. Compute the third double as `(2·frames−1)/60`:
`L + 1/60` gives a different last bit of the mantissa.

**`MediaStartTime` and `MediaExtents[0]` are one quantity in different units.**
On a clip the frames are divided by the nominal 30, in the media pool by the real 29.97.
For `01;57;31;11` that is 7044.3 versus 7051.3443, a discrepancy of seven seconds.

**`Flags=2` — the clip is off.** This is how the multicam layout is stored: camera 1 is always
visible, camera k>0 only on its own pieces. The analogue of `<enabled>` from XML.

## What is done

Word-by-word titles are assembled and work: 195 of them on a real video, the timings and the
yellows taken from our XML (`parse_full` + `auto_highlights`), the yellow
`[1, 0.9176, 0]` and the squeezing of long words (threshold 14 characters) — as in Premiere.
Verified by the user in Resolve.

Not carried over, needs clarification against the reference:

- **font** — in AE it is the PostScript name `SFPro-CondensedSemibold`, Fusion wants
  a family + a style separately. `Open Sans Bold` from the template was left;
- **point size** `0.0729` (140 px ÷ 1920) and **position** `{0.5, 0.4036}` — a recalculation, not
  a transfer: in Fusion `Size` is relative and its relation to the frame height is not
  verified.

The way to clarify any of them is the same one that solved this whole task: ask for
one title to be fixed in Resolve, export `.drp` and take the exact numbers.

## Track layout in the export

Matches the convention that `parse_full` (`core/xml2ae/parse.py`) already expects — bottom-up:

```
V1..Vn   cameras 1..N
V(n+1)   PHOTO inserts
V(n+2)   VIDEO inserts
         the subtitle track — looked up by content, anywhere
```

The same for XML and for `.drp`.

**Inserts do not get into XML** — this is a trap. The list of inserts lives in the frontend's
localStorage (`c.inserts`: `start_sec`, `duration_sec`, `type`, `media`) and goes to the
backend only when `.jsx` is assembled. There are no inserts in any working XML. For the export,
take them from the same place the AE build takes them, not from XML.

## What is downloaded where

| page | XML | `.drp` |
|---|---|---|
| 1 — cutting | yes, **only it** | no |
| 2 — markup | yes | yes, with inserts and subtitles |

There is deliberately no format choice on the first page: there are no inserts and no
titles yet, and XML opens both in Premiere and in Resolve.

## A risk to keep in mind

The format is private. Blackmagic may change it in any update and will not warn
anyone. Everything described here was taken from 21.0.3 and is subject to rechecking when
the version changes. That is the price for the only format that carries graphics.
