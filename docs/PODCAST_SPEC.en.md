# Podcast → reels

A long horizontal podcast is cut into N reels. From then on a reel lives as a regular
clip: it goes into the step 1 queue, and the person starts the AI cut.

```
source (file | YouTube + track choice) → download → transcription (words + timings)
 → AI: "N pieces, start/end" → podcast card → editor window (16:9)
 → [Convert to reels] → shot cuts → camera angles → heads → who speaks → speaker centre
 → vertical reel file → entry in the step 1 queue (one camera, absolute path)
```

## Owner decisions (2026-10-10)

| Question | Decision |
|---|---|
| Where in the UI | an ordinary card on step 1, right after Google Drive; hidden in ⚙ → "Cut" → "Cut page", like Google Drive |
| Source | a file on disk OR a YouTube link; download at the highest quality; track — the original by default, but an auto-dub track can be chosen |
| Folder | the podcast has its own folder; reels go into the camera 1 folder of the speaker, like his clips; without it, `reels/` inside the podcast folder |
| "Brains" for choosing pieces | the `podcast` step in ⚙: AI profile + reasoning level, like the other steps |
| Language | the transcription engine is chosen in ⚙ → "Podcast", like the cut engine |
| Wide shot, 2+ people in frame | the centre goes to the SPEAKER (by voice) |
| After conversion | reels go into the step 1 queue; the cut is not started |

**The reframe is baked into the reel file** (by shot cuts), not through the `frame` of the
speaker profile: the profile frame is one per camera and shared by all episodes, while the
angles of each podcast are its own. The cut and the AE assembly do not change — for them a
reel is a clip on one camera.

## Data

Podcast folder: `<podcasts folder>/<slug>/`, where `slug` is a safe name derived from the
title (Latin/Cyrillic, digits, `_`; no `\/:*?"<>|`; at most 60 characters).

| File | What |
|---|---|
| `podcast.json` | podcast project (schema below), written only through `fileio.atomic_json_dump` |
| `source.<ext>` | downloaded video (YouTube). A local file is NOT copied — its path is kept in `source` |
| `audio16k.wav` | audio for transcription (16 kHz mono), recreated when the source is newer |
| `words.json` | transcription words `[{"w","start","end"}]` (format of `asr_backends`), seconds from the start of the source |
| `cams.json` | camera analysis of the source (`core/podcast_cams.py`): cuts of the whole source, the angle of each, confidence and heads per angle (`angle_info`), heads of unclear cuts (`shot_heads`). Valid while the source is unchanged (path, size, mtime) |
| `reels/<slug>_<NN>.mp4` | vertical reel after conversion, `NN` = piece number starting from 01 |
| `reels/<slug>_<NN>.reframe.json` | shot cuts, angles, heads and the frame per shot — for debugging and re-runs |

```json
{
  "v": 1,
  "title": "Episode title",
  "slug": "episode_title",
  "url": "https://youtu.be/…",          // empty for a local file
  "source": "C:/…/source.mkv",           // absolute path to the video
  "audio_track": {"format_id": "251-1", "lang": "en", "original": true},  // null for a file
  "duration": 5321.4,
  "reels_wanted": 5,
  "status": "new|downloading|downloaded|transcribing|transcribed|choosing|ready|converting|converted|error",
  "error": "",
  "asr_engine": "whisper",
  "lang": "ru",                           // transcription language (detected or set)
  "segments": [
    {"id": "a1b2c3", "start": 812.40, "end": 871.05, "title": "About money",
     "why": "a complete thought with a conclusion", "reel": ""}   // reel — mp4 path after conversion
  ]
}
```

Invariants: `0 <= start < end <= duration`; pieces do not overlap; `id` is stable under
boundary edits (editing in the window does not change `id`); after conversion `reel` is not empty.

## Settings (`ai_config.json`)

- the `podcast` step in `STEP_REASONING_DEFAULT` (`"medium"`) and `STEP_TITLES` (`"Podcast"`) —
  this is how `step_profile("podcast")` / `step_reasoning("podcast")` work out of the box;
- `active_podcast_asr` — the transcription engine from the `asr_backends.engines()` catalog,
  default `"whisper"` (any engine, not necessarily `cut: true`);
- `podcast_lang` — `"auto"` (default) or a language code; with `auto` whisper detects it itself.

## Download (YouTube)

- track check: `python -m yt_dlp -J --no-playlist <url>` → list of audio formats:
  `format_id`, `language`, `abr`, original/dub flag. On YouTube this is the word in `format_note`
  (`original` / `dubbed` / `dubbed-auto`) and `language_preference` (higher for the original).
  Tracks without a language form one "default" group.
- video — best by height, then bitrate: `-f "bv*+ba[format_id=<id>]/bv*+ba/b"`;
  merged into `mkv` (`--merge-output-format mkv`): on YouTube 4K exists only as VP9/AV1,
  and remuxing it into mp4 is fragile. Browser preview goes through the existing
  `/api/preview_proxy` door (720p H.264), not the source.
- progress — `--newline`, lines `[download]  12.3% of …`; idle timeout as in rclone
  (10 minutes without output → kill the process tree).

## Choosing pieces (AI)

The model input is the transcript as phrases with time marks `[mm:ss] text` (a phrase is the
words up to a pause of ≥ 0.6 s or up to the end of a sentence). The answer is JSON
`{"segments":[{"start":sec,"end":sec,"title":"…","why":"…"}]}`, exactly N pieces
(more — take the first N in answer order; fewer — as many as there are, plus a warning).
Default piece length is 30–90 s (in the prompt). Code after the answer:
1. boundaries stick to words: `start` → start of the nearest word, `end` → end of the word
   followed by a pause (no cutting in the middle of a phrase), search window ±3 s;
2. overlaps — the second piece starts after the first; degenerate ones (< 5 s) are dropped;
3. sorting by `start`.

A long podcast that does not fit the model's context for the step (estimate: characters / 3
greater than `ctx_limit` from the catalog): v1 honestly refuses and suggests choosing a model
with a larger context. Overlapping windows — only if we actually hit the limit.

## Conversion to reels (per piece)

The owner, 2026-10-10: "we have tracking and roto — tracking follows where the person is in
the frame, and the code puts them in the centre." Heads are found by OUR tracker
(`core/headtrack.py`, RVM mask); a separate face detector is not needed.

1. **Shot cuts** — `ffmpeg -i src -vf "select='gt(scene,0.1)',showinfo" -f null -` over the
   WHOLE source, once (in the camera analysis): `pts_time` values; a shot shorter than 0.4 s is
   merged with its neighbour. A piece takes the shots cropped to its own bounds.
2. **Camera analysis** — `cams.json` (see "Data"): once per source, under the shared lock right
   after transcription. "Continue" and the conversion compute it if the file is missing or invalid
   (the source's path, size or mtime changed). Middle-frame thumbnails of the shots, 64×36 grey →
   angles (mean absolute difference < 18 — one angle) → reassignment: each shot goes to the angle
   with the NEAREST reference (the reference is the angle's medoid), two rounds. On the owner's
   recording of 2026-10-11 a wide shot stuck to the guest's angle (17.7 against a threshold of 18,
   against 7.7 to its own angle); the nearest reference fixes this.
3. **Confident and unclear angles** — an angle is confident if it has 3 shots and 20 s or more.
   Its samples are three shots: the first, the middle and the last in order; a 1 s window around the
   middle of each shot, cropped 0.05 s inside it. The heads of a sample are the modal number of heads
   over its points (points without heads do not count); positions are medians of `hx` and `top` over
   the points with that number. Samples agree if the number of heads is the same and the spread of
   `hx` of each head is ≤ 0.05: then the angle is confident and its heads are the medians of the
   samples (`angle_info`). Unclear — a small angle, or samples that disagree: they get RVM on each of
   their shots (`shot_heads`).
   RVM is at most two `headtrack.track` calls (5 per second, `heads=True`) for the whole source: the
   first on the samples of confident angles (three confident angles — nine windows), the second on
   the unclear shots. No unclear shots — no second call.
   The heads of a shot for the frame: `shot_heads[i]` if the shot is unclear, otherwise the heads of
   its angle. Converting a piece does not call RVM.
   Heads in points (`[[hx, top], …]`) come from the top-edge profile of the mask: for each column the
   topmost row of the mask, smoothing, local peaks ("crowns") with a spacing of ≥ 8 % of the width
   and a rise of ≥ 4 % of the height. Two people sitting side by side often merge into one blob in
   the mask — that is why profile peaks are used, not connected components.
4. **Who speaks** (an angle with 2+ heads) — by mouth movement: frames at 10 per second, for
   each head a window below the crown (height ≈ head width), we compute the frame difference
   in the lower half of the window minus that in the upper half; the speaker is the head with
   the maximum in a 1 s window. With no speech in the window (by words) or a lead below 15 % —
   the previous speaker; a change happens no more often than once per 1.5 s.
5. **Frame** — piecewise constant: a 9:16 window over the full height, centre X = head
   (one head — its median; several — the speaker's), pressed against the frame edges. A change of
   speaker inside a wide shot is a hard cut (like an editing cut), with no panning.
6. **File** — one ffmpeg pass: `-ss a -to b -i src`, `crop=w=CW:h=ih:x=X0` +
   `sendcmd` (a command file `t crop x X;` at each change), `scale=1080:1920:flags=lanczos`,
   video codec `encoders.pick("master")` + `codec_args`, audio — AAC 320k (Premiere does not
   take opus from mkv). Output `reels/<slug>_<NN>.mp4`, next to it `.reframe.json`
   (shot cuts, angles, heads, speakers, frames).
7. **Queue** — an entry in the step 1 queue as one camera with an ABSOLUTE path;
   `build_pairs` joins `os.path.join(camdir, abs)` into `abs` itself, so the entry survives any
   camera switch. A queue mixing two-camera pairs and a single-camera reel must be cut in one
   run. The cut is NOT started.

## Jobs and locks

- download — its own job (like `GDJOB`); the cut JOB is not occupied;
- transcription, camera analysis and conversion to reels — GPU/heavy: under the shared cross-process lock
  (`_cross_lock_acquire`), like the roto preview;
- AI choosing — no GPU lock (cloud), but cancellable through `aicut.cancel_call`.

## What is done

- **Project and download.** The podcast project, YouTube tracks (original or auto-dub), download with
  progress and a stall watchdog; a local file is used by its path, without a copy.
- **Transcription and choosing pieces.** Transcription through `asr_backends`, phrases with time marks,
  the AI picks N pieces, boundaries snap to words, overlaps and degenerate pieces are removed.
- **Job and cancel.** Routes and the job `PDJOB`, the status in `podcast.json` (continues after a
  restart), cancel, the shared lock for the heavy steps.
- **Block on step 1.** An ordinary card under Google Drive, hidden in ⚙ → "Cut" → "Cut page". Podcast
  rows: edit (pencil), resume, bin. "All reels to the queue" is one job over all podcasts of the folder.
  "Podcast" in ⚙: AI profile, reasoning level, engine, language.
- **Editing window (16:9).** The video (720p proxy in the shared progress line), the list of pieces and
  the "Whole recording" timeline: waveform, words as bubbles, zoom, dragging. Editing: C/S cut,
  D/Delete/Backspace remove, I/O set the edges at the playhead, a double click makes a new piece.
  Undo and redo, autosave, selection of several pieces. The timeline is the same as in step 1 and in
  the inserts (see `docs/ARCHITECTURE.en.md`).
- **Camera analysis once per source.** Shot cuts over the whole source, angles from thumbnails, `cams.json`.
  RVM is called on samples of the confident angles and on the unclear shots; converting the pieces does
  not touch RVM. Heads are taken from the top crowns of the mask profile, with a tolerance by window width.
- **Speaker.** By mouth movement on every angle with two or more heads; in a wide shot — the face at full
  resolution and a window from the head width in the mask.
- **Conversion to reels.** A 1080×1920 file (H.264 master profile, AAC 320k), `.reframe.json` next to it
  for debugging. The reel joins the step 1 queue as one camera with an absolute path; the AI cut is not started.
- **Reels folder.** The camera 1 folder of the speaker; without it, `reels/` in the podcast folder, with
  `fallback: true` in the answer.
- **Bin.** Deleting a podcast moves it to `<podcast folder>/_reelsi_trash/` with `trash.json` (`kind:
  "podcast"`); restore puts it back; emptying for good is in ⚙ → "Cut". An external source goes with the
  "And sources" tick, if no other podcast of the folder uses it. Reels in the queue from the podcast's
  folder leave the queue with it, with a warning.

Measurements the decisions rest on (live runs of the owner): the whole episode — 4 confident angles,
RVM 169 frames instead of 1374 when analysing by samples; the mouth-based speaker and baking — 62 s → 27 s;
two reels from the episode — 45 s, an angle switch guest/host; a wide shot of 2:18 recognised correctly.

## What is open

- A wide shot where two people speak at once (laughter): the speaker is chosen ambiguously.
- A shared source is searched only among the clips of the same XML folder (see `docs/KNOWN_ISSUES.en.md`).
- Old podcast bin entries without `trash.json` are not shown.
- A long podcast that does not fit the model's context is refused with a hint; there are no overlapping
  windows until we actually hit the limit.
- The AI cut of reels is not started by itself: that is the owner's decision, not an unfinished part.
