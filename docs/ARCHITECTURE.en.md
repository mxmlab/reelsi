# Reelsi — architecture, how things work, roadmap

> **Note**: This document is a concise English overview. The full and authoritative reference is [ARCHITECTURE.md](ARCHITECTURE.md) (in Russian); if anything diverges, the Russian version takes precedence.

> Read THIS file first in a new session. It holds the big picture, data contracts and
> gotchas. Point specs live in `README.md`, `docs/ROADMAP.md`, `docs/HIGHLIGHT_SPEC.md`,
> `docs/INSERTS_SPEC.md`, `docs/INTRO_SPEC.md`.

## What it is and where we're going

A local "CapCut for PC" that works on top of **Adobe Premiere + After Effects**.
It automates the routine of editing short vertical videos (talking-head, multicam:
up to four cameras, verified on two). Everything runs locally on GPU, nothing goes
to the cloud.

The core is **AI editing**, already wired in and working:

- **cutting** — the main engine **GigaAM whole-file** (`core/gigaam_cut/`): one model
  listens to the whole video and returns words with timestamps, the LLM (27b)
  decides what to drop, and code finishes the mechanics. One pipeline combines
  everything the classic path did with separate modules: silence, repeated takes,
  sighs, coughs and "khe" — plus audio-based cut refinement and camera layout;
- **markup** — `core/aicut/`: highlight words, inserts (semantic library lookup or
  AI-generated image/video), intro;
- **assembly** — `xml2ae + styles + roto (RVM)` → `.jsx` for After Effects.

Same strategy as always: **remove a piece of manual work every day**. Only the
creative end stays manual — fine-tuning in Premiere and rendering in AE; from
cameras to `.jsx` everything is automated.

Note: the project directory (the folder one level above `reelsi/`) is **not a git
repository**. The repository is the **`reelsi/`** folder (its own `.git`, branch
`main`). Code edits and commits happen here.

## Pipeline (top to bottom)

Blue — Python tools (automatic), yellow — manual work, gray — files:

1. **Cameras 1–4** (`.mp4/.mov`, folders `камера1`, `Камера2`, …) — input.
2. **Reelsi · "Cut" tab** → main engine `core/gigaam_cut/`:
   `sync → GigaAM whole-file → 27b decides → post-pass → audio refinement →
   sighs/coughs → assign → xmlbuild` (+ draft `.draft.mp4`).
3. **XML for Premiere** — in one run everything is cut out: silence, takes,
   coughs and sighs; multicam is mixed (subtitle graphics are built in step 2
   "Markup" on the finished XML).
4. **Reelsi · "Markup" tab** → `core/aicut/` (LLM): highlight words, inserts (library or
   AI-generated image/video), intro.
5. **Premiere — final fine-tuning** (manual, optional; editor edits are remembered
   in `user_overrides` and respected on re-cut).
6. **Reelsi · "After Effects" tab** → first the **scene plan** (`/api/scene` — all
   assembly math without `.jsx` and roto), which the step-3 player draws in the
   browser and edits by dragging; then `xml2ae + styles + roto (RVM)` → `.jsx`
   assembled from the approved plan.
7. **Render — headless**: the "Render" button assembles the project and drives
   `aerender` (Windows only, trio `api/render.py` [routes] → `core/render_job.py` [orchestration] → `core/aerender.py` [stateless engine]); After Effects no longer has to be
   opened by hand.

Only stage 5 (and even that optionally) remains manual — the creative end. The manual
visit to After Effects is gone from the pipeline: only the render delivery is left there.

## Two cutting engines

### Engine 1 — GigaAM whole-file (main, package `core/gigaam_cut/`)
Launched via the "AI Cut" button on page 1, or custom stage selection via the "Custom" modal.
Entry via `core/omni_cut.py` (default `gigaam`). The ASR engine is selected in settings
(`active_cut_asr`, only engines with `cut: true`). One pipeline combines everything the
classic path did with separate modules — details below.

### Engine 2 — classic (legacy: VAD + Whisper, `process_pair` from `core/cutjob.py`)
Kept for compatibility and accessible via the VAD branch ("Pauses" stage = `loud`):
`sync` (audio+sync) → `vad` (pause removal by loudness) →
optional `transcribe` (Whisper large-v3, cache `<src>.words.<md5>.json`, loaded only
when deduplication is enabled) → `align` (repeated-take removal, multicam layout) →
`xmlbuild` (Premiere FCP7 XML). Output: numbered `NN_stem.xml` into `Reelsi_out`.
Subtitles are not generated during cutting in either branch (built by "Markup all" on the finished XML).

### How engine 1 cuts
GigaAM v3-CTC listens to the ENTIRE file and returns word-level timestamps itself
(`word_timestamps=True` — text and timings from one model, wav2vec2/forced-align
NOT used). We listen in ~18s windows (`_transcribe_words_manual`, win=18/search=6):
the seam goes into the quietest point of the last `search` seconds so a window
edge never cuts a word; `torch.cuda.empty_cache()` after each window (on Windows
WDDM otherwise — access violation). **pyannote-longform was removed 2026-08-10**:
gated `segmentation-3.0` weights + Windows bugs in pyannote/speechbrain — only the
windowed fallback remains (it was the fallback before too: ~24s windows sewn at
the quietest point). Then 27b sees the whole word-level text (words without
numbers — the position is set by the text itself) and returns drop-ranges
(take re-shoots, NG takes, parasites);
silence > 0.8s is always cut automatically (like VAD). Thresholds are constants in
`core/gigaam_cut/tune.py` (SILENCE_SEC/MIN_KEEP/REPEAT_N/REPEAT_WIN and the rest;
`GAP`/`PAD` were removed).

Package layout since 2026-08-06 (was one 2310-line file): `tune` — thresholds and
everything that reads them; `takes` — takes and the code post-pass; `asr` —
transcription and alignment; `decide` — decision prompts; `pipeline` — the `run()`
orchestrator (the `seams` module — seams and draft cross-check — was removed
2026-08-10 together with the self-check loop).

`tune` is split by CONSTRAINT, not by topic, and this must be known before
splitting it further: `apply_speaker` writes thresholds straight into THIS module's
globals, so `from .tune import SNAP_DB` in a neighbor module would give a
default bound once at import — the speaker profile would stop applying silently,
without an error. All readers of rewritten thresholds live in `tune`; whoever needs
a threshold from outside takes it through the module (`tune.SILENCE_SEC`), not by
name. For the same reason SPEAKER-MUTABLE thresholds are not re-exported on the
package facade (the facade does re-export the rest — see `core/gigaam_cut/__init__.py`):
use `gigaam_cut.tune.SNAP_DB`.

**What the model answers — one mode: `markup`.** (The `index`/`quotes` modes and
`AUTOCUT_DECIDE_MODE` were removed 2026-08-10 as unused.) The model sees the
ENTIRE video text and returns it whole, with what to drop wrapped in
`[ ]` (`decide_markup` + `parse_markup` + `align_markup`): the cut position is set
by the text itself, no arithmetic, and the decision is visible right in the log.
Alignment to our words is difflib; where the model rewrote/skipped a word, it
STAYS (silently cutting what the model never showed is not allowed), coverage is
logged. Measured on C1353-1356: 100% match on all clips. markup needs neither
arithmetic nor self-quoting — the model just puts brackets in the text it was
given. The post-pass stays on even in it (`REELSI_LIGHT_POST=1` — mechanical
cleanups only): even with reasoning the model sometimes takes all three takes at
once ("чтобы не дать" ×3 -> "разрушить луковицу" left), sometimes cuts the
continuation. When the model is right, `force_takes` makes the same decision and
changes nothing.

**Code post-pass (`postprocess`, added 2026-07-22).** Key finding from the C1353
cut analysis: 27b is ONE call for the whole video and it CANNOT count indices. In
`notes` it writes everything correctly ("keep the last clean take"), but puts the
neighboring range into `drop`: it kept the early take of a duplicate, a word stub
at a cut ("питание воло | волосяных"), 1-2 frame islands, and once deleted the
intro hook entirely (drop 0-44 instead of 14-20). Prompts don't fix this, so the
MECHANICAL part of the decision was taken from the model into code, leaving it the
semantic part (what is garbage, what is off-topic, what is NG). After
`decide_markup`:

- `find_takes` — detects FALSE STARTS: the segment start repeats the start of a
  recent one (>=2 words, with `_same`/`_lev1` tolerance for stubs "воло"/"волосяных"
  and ASR typos "фолликулы"/"фоликулы"). We look for a repeated START, not equal
  takes: the speaker re-shoots however it comes out, the period is irregular.
  Window `TAKE_WIN` = 12s — beyond that it's a meaningful repeat, not a re-shoot.
  ENUMERATION is not a false start — see `align.is_enumeration` below;
- `force_takes` — inside a cluster the LAST take stays, whatever 27b decided.
  If the model dropped the cluster ENTIRELY (the quotes mode does that — it
  honestly lists all three takes), the last take is RETURNED: on C1355 otherwise
  the whole story "ещё пять лет когда я ездил в штаты выступать" disappeared. Only
  blocks with profanity/NG markers (`NG_MARKERS`) are not returned — there the
  model is right. The clean take is extended by `_take_tail` to a PAUSE, not to the
  cluster boundary: the boundary is just where the match with the abandoned take
  ended ("используем его уже" matched, "очень давно" no longer);
- `veto_unique_drops` — at the edges of every cut range it rewinds words that
  belong to no cluster and returns such a tail if it's long (>=5 words and >=1.5s).
  Real bad footage ends in a re-shoot, so it lies in a cluster; unique connected
  speech is not a take — it must not be cut;
- `heal_fragments` — 27b left the phrase start and cut a short continuation
  ("и список" without "самых опасных", "а с" without "максимальным"): if a SHORT
  (< `HEAL_SEG`) kept piece touches a short cut tail that is in no cluster — return
  the tail. Parasites from `FILLERS` are not dragged back: the model drops them
  correctly;
- `dedupe_repeats` / `dedupe_fragments` / `drop_truncated` — finishing the rest
  (adjacent repeat → EARLIER copy; fragment not adjacent → LATER copy, text
  unchanged; word that is a prefix of the next one);
- **enumeration gate `align.is_enumeration`** (2026-08-04, complaint "перечисление
  вырезается"): "где он сделал вот это, А где он сделал другое" — the start
  repeats verbatim, and `find_takes`/`dedupe_fragments` saw a false start, cutting
  everything down to the second occurrence. Enumeration is recognized by TWO signs
  at once: a linking conjunction before the second take (`ENUM_LINKS`) AND the
  first has its OWN meaningful tail (not a parasite and not a word stub of what is
  about to sound whole). The candidate is expanded LEFTWARD before the check: the
  detector catches the middle of the repeat ("он сделал" instead of "где он
  сделал"), and the conjunction would otherwise be inside the matched piece. The
  gate is common to all paths — `align.find_restarts` and the SSM text gate
  (`ssm.text_has_repeat`) use it too, the rule is duplicated in the cut prompts;
- `drop_micro_keeps` — a ONE-WORD piece shorter than `MIN_KEEP` = 0.6s (two words
  in a row are already content: "во вторых" takes 0.56s) and any piece shorter
  than `MIN_ISLAND` = 0.35s ("а с" = 0.20s — garbage at any word count).

`NG_MARKERS` work ONLY in return paths: code never resurrects profanity and NG
lines the model dropped, but also doesn't cut profanity it kept (the hero curses
on topic — that's content). The post-pass cannot cut by dictionary at all.

**Audio-based cut refinement (`refine_keep`, 2026-07-22).** Everything above works
with TEXT, but the complaints were about SOUND: "остались места без звука и
вздохи" and "обрезаются немного слова". Measurements on C1353/1355/1356 confirmed
both: 6-8s of silence accumulated inside kept pieces (pauses shorter than
`SILENCE_SEC` don't pass the threshold), and 28-30 cuts per clip landed INSIDE a
word (CTC gives boundaries with ~40ms accuracy). So after interval assembly there's
a pass over the wav itself:

- **boundaries by sound, not by a fixed offset**: from the word edge we walk to
  the END OF THE WAVE within `EDGE_IN_MAX`/`EDGE_OUT_MAX`. Measured: the sound
  stretches after the CTC "word end" at 62-83% of words (median 80-120ms, up to
  400ms), so a fixed 100ms is simultaneously "an offset everywhere" and clipped
  tails. Now "выступать" gets +25ms, "давно" +211ms. Wave end = QUIET,
  `QUIET_RUN` quiet frames in a row (2026-07-28): on the first quiet frame the cut
  used to land in a dip INSIDE the wave — the closure before "п/т/к", a syllable
  junction — and the word sounded clipped. The same `QUIET_RUN` measures word tails
  in the speech mask, otherwise the extended wave tail is considered a hole by the
  mask and the second stage cuts it back, mid-decay. There may be no quiet at all
  (the next word starts right away — a cut take) — then, as before, we land at the
  QUIETEST spot: otherwise the cut hits another word's attack and its stub is
  audible. A word belongs to a piece only if its MIDDLE is inside: by touch the
  neighboring word was pulled in, which the user deliberately cut (up to 600ms);
- **piece start — from WAVE ENTRY, not from CTC start** (`_start_edge`, 2026-07-31).
  Complaint: "начало бывает обрезает не на полной тишине, руками много довожу".
  C1414 analysis found the root: CTC word start wanders ±150ms in BOTH directions
  relative to the sound. For "я" it was 160ms late — the `EDGE_IN_MAX` window hit
  solid wave, no quiet, and the fallback "quietest frame" landed on the word
  attack; for "транболлона" 130ms early — a noisy inhale at that time, and the cut
  landed on the inhale although the real silence was LATER than the CTC start. So
  the word entry is found by sound: voice is louder than `ONSET_DB` above the floor
  and not quieter than the word peak minus `ONSET_FALL` (inhales/room noise on real
  clips 12-18 dB, speech 30-45), a quiet attack is extended back up to `ATTACK_MAX`,
  then `START_PAD` offset. Cross-check with the user's manual edits (8 clips, 179
  pieces): median deviation from his boundary 60 -> 20ms, starts landing on sound
  19 -> 7, and in 6 of the remaining 7 he also cuts by sound himself (continuous
  speech, no silence there). Piece count and content don't change — only excess air
  is removed (~1s per clip). The SPEECH MASK (below) was NOT switched to wave entry:
  the error cost there is higher — on the same clips a wave-based mask cut the
  starts of quiet words ("клеточная", "поэтому") and they went into holes as
  inhales;
- **holes without speech**: inhales, "khe", smacking and knocks GigaAM does NOT
  transcribe, and that's exactly what gives them away — there is sound but no words
  on it. The speech mask is built from words with `WORD_PAD` margin, everything
  outside it longer than `HOLE_MIN` is cut, `HOLE_AIR` air stays at the edges.
  IMPORTANT: the mask is expanded BY SOUND, not by rigid padding — otherwise a
  stretched letter ("давно" sounds 200ms after the CTC word end) counts as
  non-speech and gets cut, exactly what was asked not to cut. Spectral thresholds
  (energy share >3kHz) were tried — the word mask is more reliable: a cough can be
  louder than speech and isn't caught by loudness. **QUIET frames are not speech
  for the mask** (2026-07-31): both the wave extension and `WORD_PAD` easily drift
  into a pause (the CTC word end is often past the wave end), and a masked pause
  stopped being a hole and stayed in the piece. From the user's manual edits (6
  clips): of 40 ranges he cut by hand, 22 were exactly this silence — now they go
  away by themselves, at the cost of 0.22s of foreign (also silence). The silence
  threshold is trusted only if it's 6 dB below the voice median: `floor` is the
  20th percentile of the clip, and on dense speech it drifts into the speech
  itself. Audible sighs and "khm" are not caught by this (they don't differ from a
  word tail by loudness or HF share — measured: distributions overlap fully, a
  cough is sometimes louder than speech) — there's a separate module `core/breath.py`
  for them, see below.

**Thresholds are calibrated against the USER's MANUAL cutting**
(`NGAutoCut_out/0{1,2,3}_C135{4,5,6}`, it is the reference here). What his files
showed: no hole >= 0.20s inside his pieces (max 1-2 ranges of 0.15-0.19s) ->
`HOLE_MIN = 0.15`; he places boundaries ~25ms before a word and ~82-117ms after ->
`EDGE_IN_MAX`/`EDGE_OUT_MAX`. The first version looked for "the quietest point in ±150ms"
and stretched his dense cut by +2s — abandoned. Check: `refine_keep` applied to his
own pieces doesn't change their count or length (±0.5s), boundaries diverge by
33-41ms (2 frames at 60fps). And if his pieces are merged into semantic blocks (as
AI returns them) and refined — you get his granularity: 18->24 vs his 24, 13->19
vs his 21, 12->21 vs his 23. That is, "many pieces" is normal: pauses and inhales
inside a phrase are cut, and one semantic block becomes 1-2 pieces.

**Sighs and "khe" (`core/breath.py` + `tools/train_breath.py`, 2026-07-31).** A separate step
AFTER cut refinement — because external models work here, which may be absent from
the environment, and failing mid-job because of them is not allowed (`available()`
-> cutting proceeds as before). Three sources, each about what it's strong at:

- **Silero VAD** (~2 MB, ONNX, CPU) — "speech / not speech" every 32ms, 1s per video;
- **CED-tiny** (AudioSet, 5.5M) — a range **in isolation** (placed in the middle of
  10s of silence). Speech vs non-speech separates perfectly (AUC 1.00), long sighs
  it calls Gasp/Breathing. GOTCHA: CED returns ALREADY probabilities, a `sigmoid`
  on top collapses everything to 0.5. Tiling a range instead of silence gives
  periodic hum -> "Music"; a sliding window over the video is useless (AUC 0.53) —
  neighboring speech fills the window;
- **acoustics** — loudness, HF share, duration, position in the piece, pauses around.

No single feature separates a sigh from a word tail (best AUC 0.84), so a
**gradient boosting** model decides, trained on the USER's MANUAL EDITS:
`project.json` newer than `cuts.json` = the video was edited by hand, and the
difference between auto-cut and his version is ready-made labeling (34 videos, 3787
candidates, 240 ranges removed by him). The model ships to production DISASSEMBLED
into `data/breath_model.json` (tree thresholds and leaves): sklearn is only needed for
training. Honest evaluation — thresholds are tuned on N-1 videos and measured on a
held-out one: p>=0.9 -> 22% of findings at 73% precision, p>=0.7 -> 41% at 50%.

Hence TWO operating points instead of one: `P_CUT` (0.95) — we cut ourselves,
`P_MARK` (0.5) — write to `<stem>.breaths.json`, the cut editor draws an orange
stripe, clicking it cuts the range (`/api/breaths`, `edBreathAt`/`edCutRange` in
`app/70-editor.js`). Plus the hard guard `SPEECH_MAX`: speech probability above
0.25 is NEVER cut, no matter the model confidence — a false label is free, a false
cut costs a word. False-positive analysis confirmed the gate: their speech
probability is 0.04, i.e. also not speech, the user just didn't clean it.

Retraining: `python tools/train_breath.py` (wav+word cache in `_breath_cache/`,
34 videos ~30s per transcription). Every cut edit in the editor adds labeling —
worth retraining as videos accumulate. Cut cost: ~1.6s per video (Silero 1.0s +
CED 0.5s), plus one-time model loading.

The function also returns `parents` — which source piece each new piece came from.
**Camera layout doesn't change**: `assign_cameras` runs BEFORE refinement, over
semantic pieces, and sub-pieces inherit the parent's camera. Otherwise the camera
would jump right on an inhale mid-phrase (to neighbors it always gives different
cameras). Tests: `tests/test_refine_keep.py` on synthetic sound
"word · pause · KHE · pause · word".

Words returned by seam repair are passed to `protect`, otherwise self-check
iterations loop. Tests: `tests/test_gigaam_postprocess.py` — there's the user's
reference on a real C1355 piece (`fixtures/c1355_takes.json`): four takes in a row,
manual labeling "drop 32-58 and 68-91, keep 59-67 and 92-100", and three tests
check that the code reaches it from any starting state — the model dropped
nothing / dropped everything / kept an early take. The rule "the LAST take stays
from repeats" is duplicated in `DECIDE_MARKUP_SYS` too — it was in
`core/omni_cut.py`/`aicut` but got lost in the move to the word-level path, which is why
the cut used to take the first take.

The self-check loop (GigaAM re-listens to the cut/draft, `max_iter`,
`compare_draft` with restore/drop) was removed 2026-08-10 — in practice it made
things worse. Seam self-check remains a separate `core/selfcheck.py` step (job flag
`selfcheck`, engine — `--selfcheck-model`). Subtitles the same way: `core/gigaam_subs.py`
(subprocess, so OOM doesn't kill Flask) via `core/asr_backends.py` — the common engine
registry (Whisper of any size / GigaAM / Omni / CTC models of other languages from
`data/asr_engines.json`).

**Subtitle graphics** — `core/subtitle_blobs.py` (FlatBuffer Source Text) + `core/subs.py`:
from the XML that `core/xmlbuild.py` writes (full-resolution, 60fps, one frame = one
subtitle sample), they build `.subs.json` + `.ttf` → Premiere Essential Graphics
text objects. `core/subtitle_xml.py` — word-level subtitles INTO an already-edited
Premiere sequence. `subengine` selector: `autocut` (blobs) vs `pr` (classic
Premiere captions). Timeline alignment uses a unified rule (strictly by word midpoint `align.map_words_to_clips`), and the Premiere subtitle track is assembled via a single function (`xmlbuild.build_subtitle_track` with `SUB_FIT_CHARS = 14` font scaling). Two SRT writers (`core/subs.py` `write_srt` for AE scene plan rows and `core/align.py` `make_srt` for Premiere word grouping) are kept intentionally: each mirrors the structure of its own pipeline.

**Speaker profiles** `speakers/*.json` — a set of thresholds and a style for a
specific studio/voice; defaults are constants in `core/gigaam_cut/tune.py`
(`CUT_DEFAULTS` in `core/speakers.py`). Thresholds are written ONCE at the start of
`run()` into the module constants of `gigaam_cut.tune` — no races between jobs.
Profile with `.cut` — calibration of the cut thresholds for the speaker. Roto —
`core/roto.py` is used ONLY with a speaker profile (the mask needs a stable
"who is the hero"), off when there is no profile. Manual: `roto = off` in the
profile — the UI hides the buttons; then the old flow: mask only for generated
inserts.

**GigaAM — hard VRAM requirement (2026-07-23).** GigaAM v3-CTC and 27b (LM Studio)
cannot live in VRAM at the same time (16 GB). Scheme:
1. `gigaam_cut` "holds" 27b via stream (`core/aicut/llm.py`, `begin_call`) → GigaAM can
   work (speech in CPU only? no, GigaAM is GPU).
2. By the model: GigaAM takes ~6.5 GB, LLM 27b ~4.5 GB, together — > 10 GB.
3. If VRAM is insufficient — LLM 27b is "unloaded" (unload), GigaAM works.

**stereo / sound** — sync can work with stereo (channel fader), cutting works
with extracted mono. All audio in the pipeline is 16 kHz mono.

**legacy "silence cutting"** `core/vad.py` — energy VAD, 25ms frames / 10ms step,
threshold = floor + 18dB, min_silence 0.30s, min_speech 0.20s, pad 0.08s. Used by
the classic engine and as a fallback.

**"Old" engine** — Qwen2.5-Omni (local, ~7.5 GB VRAM in a subprocess) or cloud
Omni; the decision — 27b. Omni listens to the WHOLE file and returns word-level
timestamps like GigaAM (see `core/omni_asr.py`), but doesn't cut by itself — the 27b
decision is applied to VAD segments.

**AI profiles (LM Studio / Claude / OpenRouter)** — profiles, keys, models: see
the AI providers section below.

**Yellow words, inserts, intro: LLM markup** — `core/aicut/`: `yellow` (→
`.yellow.json`), `inserts` (→ `.inserts.txt`/generation), `intro` (→
`.intro.json`) + cut decision (`decide`). All local or in the cloud, text leaves
the machine only with a cloud profile.

**Intro** — `aicut.intro_cmd` — a separate step: {count, color} per line; applied
in assembly. Subtitles automatically skip inserts (not placed on them).

**Image generation (Nano Banana)** — `core/aicut/images.py`, `gen_image`: AI photo
generation for inserts. `images_profiles.json` + gen_video.

**Video catalog** `core/aicut/video.py` — catalog of video models, request validation,
generation (gen_video).

**Seam self-check** `core/selfcheck.py` — Whisper over the stitched keep-audio (or
GigaAM/CTC over the source), catches a seam by low word confidence. Errors — to
log, into `.cuts.json`.

**Draft render** `core/draftrender.py` — 720p without sound, for preview before
Premiere.

**Export (AE)** — `core/xml2ae/`: `to_ae_full()` assembles `.jsx`; styles —
`core/styles.py` (font/color/sounds/roto/inserts), presets in `styles/*.json`.
Verification: `core/verify_jsx.py` (without AE), `tools/verify_ae.py` + `tools/ae_inspect.jsx`
(over a dump).

**DaVinci Resolve** — `core/drp.py` — byte-level `.drp` (Fusion title), `docs/DRP_SPEC.md`.

**Intro above or below roto? (unresolved, 2026-07-27.)** First full comparison of
an assembled project with its `.jsx` (`10-16.aep`, clips ng10–ng16,
`tools/verify_ae.py`): the numbers match for all seven — words, intro precomps,
inserts, roto pieces, camera clips. In **ng13 and ng14** the "text intro N" layer
lies ABOVE roto, in the other five — below, as written here ("cameras →
inserts/intro-text → roto → subtitles"). **This project can't be used for
verification: it was already edited by hand in AE**, and the move may have been
manual. Conclusion for the method: verify a FRESH assembly, before edits —
otherwise any question about layer order ends in "don't remember, me or the
script". For now — a warning in `tools/verify_ae.py`, not an error.

UI style — Hyperstudio guideline, adapted with user edits — SEE `docs/DESIGN.md` in
the repo (the main style document): obsidian #101010, hairline borders #212121,
weight 400 + uppercase in headings, white pill = main action, 4/8px radii, no
shadows; icons — SVG strokes 1.5px (chalk/gold #6f6759), emojis banned in chrome.
Green Pulse Green #98ff38 — ONLY "done/worked" statuses (user request). All help
is in "!"-tooltips (hover and focus), no visible inline hints. Yellow #f5c518 and
photo/video colors on the insert timeline are functional data, don't recolor.

**Changed 2026-07-23: step 3 (After Effects) no longer splits cameras by
tracks.** Each camera used to get its own track (Camera 1, Camera 2...), now
camera layers go on a common "Comps" track in order of appearance (order: base
shot — Cameras 1/2, then inserts and intro). This simplifies the editor's work:
no need to scroll tracks.

**Changed 2026-07-23: "Settings" card (`#aecfg`) on step 3 is hidden** — all
assembly settings moved to the settings modal (⚙). The "Assemble" button again
builds `.jsx`, the AE file itself is no longer rebuilt automatically when settings
change.

**Changed 2026-07-21: step 3 (After Effects) can assemble SEVERAL clips into one
.jsx** — "Assemble all" (set "one for all"): `build_combined()` (comp, then all
clips inside), `AEWMODE=multi` in the `.jsx` header.

**Changed 2026-07-23: step 3 — one clip at a time again.** Fixes: `preserve=1`,
per-clip assembly, the AEWMODE block removed.

**"Assemble all" on step 3** — a clip is found by filename in `xml_state` +
`ins_data`, AE assembly is always per-clip; "one for all" is just iterating clips
one by one (modal "Export" → "Assemble all").

**Added 2026-07-16: mini-player in the camera layout (CPV — picture by edit,
sound from the selected camera to check sync)**, "Words" panel in preview
(yellow straight into XML blobs via `/api/set_yellow`, intro in the step-3 job,
word text edit via `/api/edit_word`), green "✎ edited" label on clips, the camera
layout button is hidden for 1-cam, space in modals is always play/pause, insert
library (see `core/insertlib.py`).

**Changed 2026-10-02: step 1 has ONE player — the cut editor.** The "Montage" block with a
second player on the same video is gone: two players fought over `currentTime`, and a click
far along the timeline during playback rolled back to the start of the next montage piece
(the second player drove the video by its own piece number). The playhead and the picture
are driven by `ED` (`static/app/70-editor.js`), and the volume and the "camera — cuts —
length" line moved into the editor's control row. The speaker's processed voice (the
denoise track `vt*` and the live VST plug-ins) plays in the SAME player, both when
listening to the montage and when playing the source: the sound time is the SOURCE time of
camera 1 under the playhead (`ED.cs`), and over the cut-out places the sound jumps with the
picture. `ED.cams` (set by `openPreview`) gives the voice track and the live host camera 1's
file, and `ED.voicePanel` says which "Voice" panel to read the live knobs from. The "Voice"
panel is read AFTER the editor opens on the first opening of the preview, otherwise the
clip's speaker was not found.

**The live voice host (`core/voicefx_editor.py`, `--live`)** is a separate process per clip:
it plays the track (the voice after the denoiser) through the enabled VST plug-ins in step
with the picture (commands `play`/`seek`/`pause`/`track`/`chain`/`gain`/`dump_states` over
stdin) and opens plug-in windows in the MAIN thread (JUCE cannot otherwise), without
interrupting the sound. The output stream is opened at the DEVICE's rate
(`sample_rate=None` when it refuses 48 kHz), and a streaming resampler sits at the end of
the chain: the plug-ins still compute at 48 kHz, and `write` gets the stream's rate. A
sound failure (device, stream) is reported as an `audio_error` event in the status of
`/api/voicefx_live`, and the page then gives the sound back to ITSELF instead of muting its
own voice in favour of a silent host. The host's volume is the speaker's voice volume (dB)
plus the play volume (20·log10).

**Format: live plug-ins only while a window is open (changed 2026-10-06).** The host is
raised while at least one plug-in window is open: then the sound goes through the chain
live ("turn the knobs and listen"), and the player attaches the DENOISE TRACK (without
plug-ins — otherwise the chain would be heard twice). With the windows closed, the settings
of ALL plug-ins are saved, the voice is re-baked, and ALL steps (step 1 `ED`, step 2,
step 3 `IPV`) play the FINAL baked voice (`final: true`, the versioned file with the chain)
from the cache. The reason: the host is a separate process and cannot be aligned to the
picture closer than 0.4 s — with VSTs enabled the preview lagged ("it did not finish the
ends, sometimes played more and in the wrong place"), with them disabled it was even.

Closing the last window and stopping the host (preview closed, clip changed, leaving the
step) begin with the `dump_states` command: the host reports the state of EVERY loaded
plug-in (including disabled ones — they are kept loaded and their knobs are turned too) as
a `states` event carrying `[{path, name, state_b64}]`. The server writes them into the
speaker profile BY PLUG-IN PATH in a single save (`api/voicefx.py:_save_live_dump` →
`_save_live_states`), and only then is the host killed by PID (`core/voicefx.py:live_stop`
with a hook). Without this, stopping with a window open would lose what was dialled in: only
the host process knows the state, and killing by PID gives no answer back. While the new
voice is being baked, the PREVIOUS baked track keeps playing (not the camera sound), the
voice line says "recomputing the voice…"; when ready, the `<audio>` source is swapped
without pausing playback. The host input is the denoise track if it is enabled and already
computed; the raw sound is left only for those who have nothing else to play (no cache —
while RoFormer computes, one needs to hear a voice, not silence).

Sync of the own track (`vtTick`, `static/app/60-preview.js`): a drift of 0.03–0.25 s is
absorbed by SPEED (`playbackRate` ±6 %, as with the cameras — `camTrack`), above 0.25 s by
a seek, and on pause/scrub and on a cut jump (`vtSeekAt`, from `edJump`) the position is
set exactly and at once. Seeking every 0.15 s was what sounded like "played in the wrong
place".

**Added 2026-07-23: shared volume control** on all three video previews (edit
`PV`, inserts `IPV`, layout `CPV`). An `<input data-vol>` slider in each panel,
one `MEDIA_VOL` value (localStorage `autocut2_vol`, 0..1) for all players —
`setMediaVol` writes the key, applies to ALL `<video>` (only the unmuted one is
audible) and syncs all sliders; new `<video>` take `MEDIA_VOL` at creation.

**Preview audio and the Web Audio graph (2026-10-07).** `createMediaElementSource` is an
IRREVERSIBLE door: after it an element sounds only through the graph, and a suspended
`AudioContext` (the browser keeps it `suspended` until a real gesture) mutes that path
silently — this was the "the video plays, there is no sound, and there are no errors" defect.
So only what actually NEEDS the graph goes into it, through exactly two doors, each with its
own job:

- **`audioWake()`** — the one door that wakes the context, called from the click handler by
  every player (`edPlay`, `ipvPlay`, `cpvPlay`). Before, `AUDIO.resume()` existed in EXACTLY
  one place — the MUSIC sync: the step-3 player sounded because `ipvPlay` called `musicSync`
  at the end, and nothing woke the graph for the step-1 player.
- **`voiceGraphNeeded()`** — whether the graph is needed for volume: only `VG.gain` can do the
  style's voice volume (`voice_db`), an element's `volume` cannot go above 1.
- **`voiceWiring(v)`** — a request to attach (the element exists, the decision comes later),
  and **`voiceEnsure()`** — "the graph is needed now": it wires the `VOICEPEND` queue and wakes
  the context. Only the doors where processing really sounds call it (`vtGate` with the gate
  open, `vtEl`/`vtSpareOf` when the voice track is created) plus `applyDbGains`. The single
  place that calls `createMediaElementSource` is `voiceGraphWire`.

A speaker WITHOUT processing never touches the graph: the camera audio plays directly and does
not depend on the `AudioContext` state. The camera sound used to be wired into the graph
unconditionally, on element creation even.

**Firefox — a row about the FACT, not a guess.** The footage is written `pcm_s16be` in MP4;
Chromium reads that track (measured 2026-10-07: Chrome 152 on Windows and Linux), Firefox does
not. The `<video>.mozHasAudio` property exists ONLY in Firefox, so the code asks the element
itself (`fxAudioFact`): the answer is remembered on the element TOGETHER with its `src`
(moving to the preview proxy changes the source — the old answer was not about it), and the row
"Firefox cannot read the sound of these cameras — the sound will appear with the proxy" is
raised by a DEFERRED pass (`FIREFOX_FACT_MIN = 1.5` s of playback, since before the first
decoded frame `mozHasAudio` means nothing) and is dropped once the cause is gone. In Chromium
the row never appears. The recommended browser is Chromium-based (Chrome, Edge, Brave).

**There is NO audio proxy — it was removed on purpose.** The earlier version built an `.m4a`
first and played it through a separate `<audio>` wherever the source was "unreadable". On
Windows Chrome the camera sound `pcm_s16be` IS read, so the proxy played ON TOP of the camera
sound: the voice was louder (in phase) and doubled (with an offset), and the camera was muted
only on a camera change. So it is gone front and back: no `<audio>` proxy, no
`media.probe_audio_codec`, no `_audio_proxy_plan`/`build_audio_proxy`, and no `audio` field in
`/api/preview_proxy` — the route is a plain video proxy again, and its track is aac. Guard:
`tests/test_preview_audio.py`.

**`pedalboard` is declared.** It was listed nowhere — not in `requirements*.txt`, not in
`pyproject.toml`, not in the installers, not in `doctor.py` — so turning the plug-ins on was
the only way to learn about it. It now lives in `requirements-optional.txt` and in the
doctor's optional list ("live monitoring through VST3 plugins and the output device list").
The output device list is no longer tied to it: without the package `/api/voicefx_devices`
answers with the devices the sound system reports (Windows registry,
`core.voicefx.system_output_devices`) and a `reason` field, instead of failing with "no
pedalboard"; the `reason` is shown in the panel (`static/app/95-styles.js`, `fxDeviceFill`),
not only written to the log.

**Changed 2026-08-01: "Files" panel** — a "Mark up all" button on the "Files"
tab → runs `/api/ai_yellow` + `/api/ai_inserts` + `/api/ai_intro` per clip without
stopping (there is no single "markup_all" route; the batch is a frontend loop over
the per-clip AI routes).

**Changed 2026-08-06: insert card** — slots "1" and "2" on the insert card
(image: "text on image / without text"), batch generation → slot `a`.

**Changed 2026-08-06: `aicut` — a package** (was a 2721-line file): the facade
re-exports dozens of names; CLI `python -m core.aicut yellow edited.xml`.

**Changed 2026-08-06: image generation slots** — the same subject needs a frame
with text on the image or without; "1"/"2" buttons, batch — slot `a`.

**Changed 2026-08-07: insert card — the "generate missing" button** runs as a
batch: all cards without an image → slot `a`.

**Changed 2026-08-08: `test_cases.json`** — reference cases for video
generation; video catalog `video_catalog.json`.

## Key rules (the system's pain points)

This section holds the key points of how the system really works — what breaks
most often and what must NOT be changed without understanding.

**RULE:** an operation on a CLIP takes its speaker only from the clip —
`clipSpeaker(c)` (the clip's `job.speaker` tag). The step 1 general selector is the NEW
cut and the speaker profile editor, not a clip: a clip operation has no fallback to it,
or else a speaker B clip takes speaker A's quota (7 inserts instead of 13). The server
does NOT look the speaker up from `clip.json` itself — the interface choice is the
source of truth. A new speaker-dependent feature adds its check to
`tests/test_two_speakers.py` (the list of all such features lives there too).

**RULE:** insert images/videos must live in the `Reelsi_out/` folder (that is,
always next to the XML). It's a contract: `xml2ae` looks them up by relative path.

**RULE:** the insert library (`insertlib`) indexes EVERYTHING in `Reelsi_out`
(and subfolders) — not only inserts. If a file is NOT in `Reelsi_out`, it won't
enter the library.

**RULE:** insert images for AE: allowed formats are `.png`, `.webp`. NOT `.jpg`
(AE can't read it? — to check).

**RULE:** insert slots "1"/"2" are NOT two different files: they are TWO
GENERATIONS of ONE subject (two frames for the same item — with and without
text); `insertlib` indexes both.

**RULE:** `user_overrides` — only in `.project.json`, not in `ai_config.json`
and not in `ui_state` (that's UI state, not project state).

**RULE:** `insertlib` — disable-able: you can set `insertlib=off` in config.

**RULE:** ai_config — `ai_config.json` (server-side), not `ui_state`
(localStorage) — different things: `ai_config` holds profiles/keys/model
settings, `ui_state` holds interface state. Don't confuse them.

**RULE:** AE edits — `.jsx` is assembled per clip; "Assemble all" just iterates.

**RULE:** cameras on step 3 are NOT split by tracks (see "Changed" above).

**RULE:** insert card "1"/"2" — batch generation only through slot `a`.

**RULE:** `core/verify_jsx.py` catches structure errors but NOT animation errors —
that requires a render in AE.

**RULE:** the most important: `xml2ae` depends hard on the layer order in the
template — DO NOT change the order without reassembly.

**RULE:** `xml2ae` looks files up by relative paths from the XML folder —
everything must be in `Reelsi_out/`.

**RULE:** `tools/harvest_good.py` — rebuild of the template/subtitle blobs from a
reference XML. Do NOT run without a need.

**RULE:** `core/vad.py` — energy VAD, 25ms frames / 10ms step, threshold = floor +
18dB.

**RULE:** `Nano Banana` (`gemini-2.5-flash-image`) — for backgrounds and
inserts; all AI settings are in the `mbAISettings` modal (⚙).

**RULE:** insert images for AE: `.png`, `.webp`; `.jpg` — NO.

**RULE:** `GigaAM` — word-level timestamps, `transcribe_words_whole`, not
`transcribe_whole`.

**RULE:** `_cut_breaths` — sighs/"khe" — we cut confident ones, mark disputable
ones.

**RULE:** video inserts — only `.mp4`, `.webm`; generation via
`aicut.video.gen_video`.

**RULE:** slot generation "1"/"2" — TWO frames of one subject, NOT two different.

**RULE:** `core/styles.py` — clip style presets: `base` (default), `geologica`, custom
— `styles/*.json` (priority: user → default).

**RULE:** roto — only with a speaker profile (`speakers/*.json`), off when no
profile.

**RULE:** `xml2ae` doesn't change cameras without `insertlib`? — to check.

**RULE:** `xml2ae` looks inserts up by name in `insertlib` — if the file isn't
indexed, the insert won't be found.

**RULE:** subtitles are NOT placed on inserts (they automatically bypass them).

**RULE:** `xml2ae` — the output `.jsx` is written to `Reelsi_out/` (next to the
XML).

**RULE:** `xml2ae` — the path to `.jsx` is `Reelsi_out/`, not `reelsi/`.

**RULE:** `xml2ae` — `Reelsi_out/` is the only folder where `insertlib` lives.

**RULE:** `xml2ae` — roto masks: `roto/` in `Reelsi_out/`.

**RULE:** `xml2ae` — style preset file: `styles/*.json` (see above).

**RULE:** `xml2ae` — the style preset is applied via `styles.resolve`.

**RULE:** cutting stages are described ONCE — `cutstages.STAGES`; the UI renders checkboxes from the server response and does not keep its own copy of the list.

**RULE:** cutting branch is selected by the "Pauses" setting, not by a separate engine switch.

**RULE:** ASR engine suitability for cutting is the `cut` flag in `asr_backends`, not a name or `kind` check.

**RULE:** subtitles are generated by the "Markup all" step on the finished XML; cutting does not generate them in either branch.

**Pipelined markup (`markupAllRun`, `markupPlan`).** Batch markup on step 2 ("Markup all") is orchestrated
via an asynchronous pipeline: subtitle generation runs sequentially on GPU (1 clip at a time). As soon
as a clip finishes subtitles, it is pushed to downstream queues (yellow highlights and inserts). Cloud steps
(`!step_local[step]`) run immediately without waiting for other clips' subtitles to finish, and run concurrently
with each other for the same clip. Local steps (`step_local[step]`) wait for all subtitles to finish before
starting due to VRAM limits; if both yellow and inserts are local using different models, inserts wait for all
yellow highlights to complete to avoid model thrashing. Failures in one phase do not abort other phases or clips.
A clip is marked `done` only when all requested phases finish.

**RULE:** every key of `styles.BASE` must have a UI handle. The handle lives in THREE
places: the element in `templates/index.html` (a `div#stylepart_*` tab), the read in
`fillStyleFields()` (`static/app/95-styles.js`), the write in `stEdit()` — plus the
"changed vs parent" dot in `updateStyleDiffDots()`. The guard
`tests/test_style_keys_in_ui.py` checks both directions: every BASE key is written from
`static/app/*.js` as `CURSTYLE.<key>=`, and every key written that way exists in BASE;
there is NO exclusion list — after task FB it is empty, keep it that way. The price of
missing the guard: nine keys (`insert_anim`, `insert_c2_x`, `intro_x`,
`cam1_zoom_big`, `cam1_zoom_lo/hi`, `sub_bg_anim`, `sub_bg_padmin`, `audio_fades`)
were edited by hand in `styles/*.json` for years — the UI did not know them at all.

**RULE:** a dependent setting is visible only when its owner is on, and you must toggle
it through BOTH doors — the click handler (e.g. `rotoSync()`) AND the style restore
(`fillStyleFields()`). One door = the bug returns after F5: `fillStyleFields` sets the
checkbox from the style, but the settings wrapper keeps the display from the previous
session. Done this way: `st_subbg_wrap`, `st_caption_wrap`, `st_topline_wrap`,
`driftwrap`, `rotowrap` ("Roto device", "Mask bottom", "Roto on Camera 1 only" are
visible only when "Auto rotoscope" is on, task FG).

**Style keys from tasks FE/FC/FF** (all defaults = the previous behaviour; the `.jsx`
stays byte-identical — the golden test catches it; except for an intentional change of the
default build with an updated golden and CHANGELOG.md entry): `sub_scale` (scale of the subtitle
precomp LAYER, %; the layout inside the precomp — line breaks, auto-fit, stack step —
is NOT recalculated; at ≠100 the layer anchor/position move to the line point
`[W/2, POSY]`, otherwise `sub_y` starts lying); `insert_anim` (`'zoom'` | `'rise'` |
`'none'`; `none` works under ANY `insert_style` and removes `wiggle` — the jitter is
killed by the `ins_wiggle` template substitution, not by a plan field); `insert_fx`
(`'card'` | `'white'` | `'none'`; `none` removes both the shadow and the rounding
mask, so the "Mask W/H" fields in the insert card show only under `card`);
`insert_above_subs` (bool; photo inserts are raised above subtitles AND above roto —
photos on top of everything, including the person; with the "Cam 1" style the
over-the-shoulder fly-out loses its point, a deliberate choice).

## Modules (map)

| Module | What it is |
|---|---|
| `core/cutstages.py` | **single source of truth for cutting stages** (`STAGES`, `DEFAULTS`, `DEFAULT_THRESHOLDS`), normalization, and `to_reelsi_opts` generation |
| `core/gigaam_cut/` | **Engine 1 (main)**: GigaAM whole-file cutting: `tune` (thresholds), `takes` (takes + code post-pass), `asr` (transcription + alignment), `decide` (decision prompts), `pipeline` (run orchestrator). Thresholds are ONLY in `tune`, read via the module, never imported by name |
| `core/xml2ae/` | export to After Effects: `to_ae_full()` assembles `.jsx`, `build_combined()` — several clips into one. `scene_plan` is the ASSEMBLER of the scene plan (parse the XML, call the block entry points, lay the result out by key); the plan maths itself is split into blocks: `plan_words` (word prep: index alignment, intro word exclusion, `censor_source`, `.words.json` timings), `plan_assets` (project & asset folders, asset resolver, font ladder), `plan_subs` (subtitles), `plan_intro` (intro maths), `plan_intro_tpl` (intro template substitutions), `plan_inserts` (inserts), `plan_decor` (decorations: subtitle hide on rise inserts, shadow, plate, progress bar, caption, disclaimer), `plan_audio` (sound and censorship), `plan_camera` (camera: zoom, pan, roto markup, head follow) |
| `core/xml2ae/plan_style.py` | **the style is read ONCE** into a `StyleValues` structure (`read_style`, task NO): the plan blocks take ready values from it instead of calling `_sv`/`_sv_or` on every line |
| `core/emphasis.py` | **the strength of highlighted words** for the "only strong highlights" zoom rule: the emotion of the phrase (GigaAM-Emo, `1 − p(neutral)` over a 2.5 s window) plus the stress of the word by sound (RMS, `librosa.yin`, syllable length, compared with the neighbouring words by a median/MAD z-score). Both components live in the sidecar `<stem>.emph.json` next to the XML, so switching the way (`hl_zoom_strength`) recomputes nothing. The sound is read in WINDOWS around the wanted words (`_merge_windows`), pitch and RMS once per window (`tone_track`), and the emotion model is loaded for the calculation and released right after (VRAM). `EMPH_VERSION` in the cache key is raised when the weights or the windows change |
| `core/xml2ae/precompute.py` | **precompute for the build and the preview through the SAME functions**: `head_track` (the head track of the cameras the style follows), `emphasis_precompute` (highlight strength, before the plan), `roto_masks` (masks by `plan["roto"]`; `strict` — the build fails, the preview returns what it has), `precompute` (all of it — the door of the preview button), `cached_plan` (what is already computed, from the caches and without a GPU), `_release_roto` (releasing RVM — one door for the build and the preview) |
| `core/aicut/` | LLM markup: yellow words / inserts / intro / cut decision; package since 2026-08-06; `llm.py` (begin_call, cancel_stream), `commands.py` (yellow_cmd, inserts_cmd, intro_cmd), `config.py` (profiles CRUD), `config_actions.py` (the `/api/ai_config` actions, one function per action — `set_glitch_glow`, `save_profile` and the rest; the key mask `•••…` = "key unchanged" is handled here too, via `unmask_ai_key` from `config`; the route stays thin, task NZ), `images.py`, `video.py` |
| `core/omni_cut.py` | CLI/job of AI cutting: default `gigaam`, legacy `--mode old`; `--speaker`, `--selfcheck-model`, `--no-draft`; entry for both engines |
| `core/xmlbuild.py` | Premiere xmeml assembly (cameras, segments, subtitles) |
| `core/align.py` | words→timeline, `find_repeat_ranges`, `assign_cameras`, `make_srt`, `is_enumeration` |
| `core/sync.py` | audio extraction, camera sync |
| `core/transcribe.py` | Whisper large-v3 on GPU; `get_model`/`release_model` (VRAM) |
| `core/vad.py` | speech detection, pause cutting (energy VAD) |
| `core/subtitle_blobs.py`, `core/subs.py` | subtitle graphics (FlatBuffer Source Text) |
| `core/speakers.py` | speaker profiles: cut thresholds + folder + style |
| `core/roto.py` | RVM video matting of the character (alpha masks) |
| `core/headtrack.py` | head track by the RVM person mask (task ZC): RVM over the SHOWN pieces of the camera 1 source (`fps=10`, `scale=432:-2`, recurrent state reset per piece), "head" = X centre of the top band of the silhouette. Sidecar `<stem XML>.head.json` next to the XML (path, size, mtime, `ranges`): `load_cached` is the single validity check (used by both `load_or_track` and `scene_plan`), `load_or_track` computes on a miss, `head_at` interpolates over `None` gaps. Computed in one door — the head of `to_ae_full`; `scene_plan` only READS the sidecar, so the preview needs no GPU |
| `reelsi.py` | CLI of classic cutting (VAD branch; legacy, see Engine 2): flag parsing and a call into the engine in `core/cutjob.py` |
| `core/cutjob.py` | `process_pair` and `CutOptions` — the single source of cutting defaults (VAD + Whisper) for the CLI and the API: field names follow the CLI flags and the API `opts` keys, and the translation between the two lives here rather than in ad-hoc numbers on the spot (task NY) |
| `core/cams.py` | camera folders and the videos inside them: finding `cameraN`/`камераN` directories (`find_cam_dirs`) and listing the clips of a folder (`list_videos`) — one source for the UI, the API and the CLI (task NY) |
| `core/rclone.py` | pure `rclone` functions: parsing a Google Drive link (`parse_gdrive_link` — file or folder, `resourcekey`), config and remotes, command assembly, parsing progress and statistics lines |
| `core/assets.py` | asset resolver (transitions, sounds) from `assets/assets.json` |
| `core/fonts.py` | list of installed fonts |
| `core/censor.py`/`core/terms.py` | censorship + ASR terminology dictionary |
| `core/omni_asr.py` | Qwen2.5-Omni (local or cloud) |
| `core/breath.py` / `tools/train_breath.py` | sigh/"khe" detector (Silero VAD + CED-tiny + acoustics → gradient boosting, `P_CUT`/`P_MARK`); `tools/train_breath.py` — retraining |
| `core/ssm.py` | repeat detection (self-similarity by MFCC) |
| `core/falign.py` / `core/falign_cli.py` | word forced alignment (wav2vec2), a separate process |
| `core/subtitle_xml.py` | word-level subtitles into an ALREADY edited sequence |
| `core/drp.py` | export to DaVinci Resolve (`.drp`, Fusion title) |
| `core/whisper_cpp.py` | whisper.cpp engine: binary + ggml models |
| `core/gigaam_subs.py` | GigaAM subtitle subprocess (wrapper for `core/asr_backends.py`) |
| `core/omni_review.py` | EXPERIMENT (off): Omni draft review |
| `tools/webui_test.py` | isolated UI profile on port 5098 |
| `core/umsg.py` | error codes for translation (`ERR_*` from `static/i18n/en.json`) |
| `core/app_meta.py`, `core/device.py` | paths/environment, device selection (cuda → mps → cpu) |
| `core/fileio.py` | atomic writing of JSON and text files (tmp + fsync + replace; target permissions and symlinks preserved). RULE: atomic writing lives ONLY here — `os.replace` outside this file is forbidden (guarded by `tests/test_infra_dedup.py`) |
| `core/media.py` | media duration: ONE ffprobe probe for every call site (`probe_duration`; `None` = "could not read" — no file, no ffprobe, a hang, a broken container; cached by path + mtime + size, 30 s timeout). There used to be five copies, and they diverged exactly on errors: some returned 0.0, others raised (task NB) |
| `doctor.py` | environment diagnostics; also checks the optional external binaries — `rclone` (Google Drive download) and After Effects (headless render, via `core.aerender.find_ae` — one source for doctor and the render) |
| `core/aerender.py` | **the headless render engine with no job state** (task NN): finding and driving AE (`find_ae`, `ae_running`, `short_path`), parsing `aerender` output (frame regexes, timecode, composition name, frame share), ETA and phase-duration statistics (`load_render_stats`/`save_render_stats`, `predict_aep_times`, `eta_secs`), result checks (`rendered_ok`, `comp_frames`), the default output folder (`default_render_dir`). It used to live inside `api/render.py` and was out of reach for the CLI and `doctor.py` without importing the Flask layer |
| `core/render_job.py` | **headless render orchestration and `RenderJob` state holder without Flask**: launching AfterFX and aerender with stall watchdogs, output parsing, stage queue, master project, live progress and on-the-fly ETA, "Stop". The job instance lives in the owner (`api/render.py`); guard tests forbid Flask/`api` imports |
| `core/jobstate.py` | **job state management without Flask**: job logging, journal (`job_state.json`), stage queue (`items_init`/`item_set`/`item_done`/`item_fail`, `journal_*`), progress and stall watchdog (`set_progress`/`set_stalled`), cross-process GPU lock (`job.lock`), error parsing. State instances (`JOB`, `LOCK`, `RJOB`, `PJOB`) stay with owners in `api/`; `api/_core.py` re-exports functions under old names |
| `core/insertlib.py` | insert library: XML + folder scan, `insertlib.json` index, semantic lookup; also `remove_bg` (rembg) and `nobg_path(media)` — ONE cache of a photo without background for the build and the preview (`<folder>/<stem>.nobg.png`; error of rembg or a non-image returns the source path and reports through `emit`) |
| `api/` | **shared backend**: all `/api/*` (Blueprint), JOB/LOCK, jobs |
| `api/previewcalc.py` | the preview door "compute roto and tracking": `/api/preview_calc` (the button, a background calculation through `core/xml2ae/precompute.py`), `/api/preview_calc_status` (per-chunk progress and "what is already computed"), `/api/preview_calc_cancel`. Its own state `PCJOB` and the shared job lock (`_cross_lock_acquire`), so the heavy GPU stage never runs on top of a cut, a build or a render. The body is the one of `/api/scene` and is normalized by the same door, otherwise the plan and the masks would diverge |
| `api/voicefx.py` | the speaker's voice: the denoiser, the VST chain and the live host (`/api/voicefx_live`, commands `play`/`seek`/`pause`/`track`/`chain`/`gain`/`dump_states`), the plug-in window (`/api/voicefx_host`, `_host_edit`, `_host_stop`) and baking the final voice (`<stem>.voice.<key8>.wav` plus the `.voice.json` sidecar; `/api/voicefx_bake`, `..._status`, `..._cancel`). The sound is computed by `core/voicefx.py`, the live host is `core/voicefx_editor.py` (one process per clip, only while a plug-in window is open) |
| `webui.py` | **main** web UI (port 5001) |
| `tests/` | pytest golden tests of contracts |
| `core/draftrender.py` | draft render 720p |
| `core/selfcheck.py` | seam self-check (Whisper over the stitch) |
| `core/cuda_env.py` | NVIDIA runtime DLL registration for faster-whisper on Windows |
| `tools/harvest_good.py` | template/subtitle blob rebuild |
| `tools/analyze_blobs.py` | subtitle template diagnostics |
| `tools/route_coverage.py` | coverage measurement of 88 `api/` routes by real test calls (`tests/*.py`); guard test fails on untested new routes |
| `tools/` | i18n tooling: `i18n_extract.py` / `i18n_js_keys.py` / `i18n_merge.py` |

Layer rule: `api/` holds routes and job state, the engine lives in `core/`; `core/` imports
neither `api` nor `flask`, and `api/` does not import the CLI (`reelsi.py`). Guarded by
`tests/test_layers.py` and `tests/test_api_no_cli.py`.

## Input / Output

**Input** — cameras 1–4 (`.mp4/.mov/.mxf`), camera 1 is the main one (audio is
always from it). `assign_cameras` lays the rest out: never two identical in a
row (exception 2026-07-22: on 3+ cameras a long piece ≥6s — Camera 1, but only
when there are no repeats); "least-used camera" exceptions work crudely, designed
for 4 cameras.

**Output path** — `Reelsi_out/` (the folder one level above `reelsi/`), names
`NN_stem.xml`, sidecars (`.project.json`, `.cuts.json`, `.omni.json`, `<stem>.clip.json` snapshot of clip state and `_reelsi_trash/` recycle bin with restore).

**Run** — `python reelsi/omni_cut.py --out Reelsi_out/NN.xml -cam1 ... --cam2 ...`
**Run (webui)** — the "Cut" tab → `python -m webui` — no, `webui.py`.

**XML contracts** — Premiere FCP7 xmeml; specifics: 60fps timeline; `<clip>` with
`speed:100%`, `alpha: ignore` for subtitles; the "technical tail" in images is
removed.

**Pipeline edit memory** — `user_overrides` in `.project.json`: a tree
`{ clip: { restored: [..], deleted: [..] } }` — "returned/deleted" in the editor;
a repeated omni_cut hard-protects what was returned and tells the LLM.

**Rejected inserts** — `ins_rejected` on a clip → `rejected` in `cmd_inserts`:
prompt + hard filter of similar ones.

**Preflight before render** — inside `/api/render_run`, before AE starts: file
checks (missing files, `.webp`/`.avif`, CMYK, AV1, overlaps) and a `verify_jsx`
syntax pass. The old standalone `check_before_build` route is gone; when
preflight fails, the render job refuses to start.

**Two render paths.** A set of ONE clip keeps `_run_render_single`: headless `.jsx`
with the `_render_tail` tail (queue + save + quit inside the script), `AfterFX -noui -r`,
`aerender -project`. A set of SEVERAL clips ALWAYS goes to `_run_render_combined` (one
combined `Reelsi_all.jsx`, the master executes just this file; the `multimode` radio
does not affect render and is used only for manual "Build set"). `build_combined` merges
all clips with `comps_global=True` and bin prefixes; the `verify_jsx` preflight runs once
over the whole combined file (a failing clip stops the whole set). The master script executes
`Reelsi_all.jsx` where each timeline logs `таймлайн ok:` to the master journal, collects all comps
into one render queue, saves the `.aep` into the set folder, and quits; then one
`aerender -project`. Tail rules are preserved: log file first, `om.file` after `applyTemplate`,
`app.project.save` before queue and after. Progress runs over queue items (task FA):
before `aerender` all are `render`, on "Finished composition" the next one is `item_done`.

**Camera rights since 2026-07-23** — no "main" camera on step 3: all cameras are
in the set, which one is "base" for AE is NOT determined (simplification — step 3
works with the assembly, not with cameras).

**`.jsx` assembly** — `xml2ae.to_ae_full()`: `parse_full` (timeline parse) →
`styles.resolve` (style preset) → **precompute** (`core/xml2ae/precompute.py`: the head
track, the highlight strength `<stem>.emph.json` and the roto masks — the SAME functions
the preview button calls, so the caches are shared and there is no second copy of any
calculation; the strength is computed BEFORE the plan, because the plan reads its sidecar
to decide which highlights get a zoom, and RVM is released from video memory here) →
`roto` (RVM character alpha, GPU) → substitution into the JS template `AE_FULL` → write
`.jsx`. Separately `build_combined()`
merges several files into one `.jsx` (set → "one for all").

**A set is built by several AE copies.** A multi-clip set is no longer built by one
`AfterFX` one clip after another: `ae_build_workers` (`auto` by default) starts N copies
(`AfterFX -m -noui`), each building its own share of the clips into a fresh project, and
one more copy merges the parts into a single `reelsi_batch.aep` (importing the parts,
merging the repeated footage, then the render queue as before). `auto` = min(3, clips,
free RAM / 10 GB); `1` keeps the old path. The `.jsx` is still ONE file for the whole set:
a part builds only its own timelines (`$.global.REELSI_ONLY`), so running the `.jsx` by
hand in AE is unchanged. Measured on 12 clips (AE 26.2): one `AfterFX` 1534 s (the time
per clip grew 10 → 284 s, as AE accumulates undo history), 3 copies plus the merge 237 s.
Hygiene: old part and merge logs are removed before the run, an `.aep` not re-saved by this
run fails the clips (no `aerender`), and Stop kills every copy by its own process tree.
Both paths pass `-mem_usage 40 60` (`AERENDER_MEM_USAGE`): without the limit AE takes
almost all memory, Windows swaps (+513 MB on one clip) and the render is SLOWER — 361 s
against 290/296 s; with the limit 34–40 GB stay free and the swap file does not grow.

**Step-3 verification** — `core/verify_jsx.py` (without AE), `tools/verify_ae.py` +
`tools/ae_inspect.jsx` (over a dump).

**AE VERIFICATION** — `tools/verify_ae.py` compares the project dump with `.jsx`
contracts: for each layer — that it exists in the dump; layers with `speed!=100`
or scale 404 — errors.

**VIDEO GENERATION** — `core/aicut/video.py`: video model catalog (from
`video_catalog.json`), request validation (prompt, duration), generation:
`gen_video` → subprocess ffmpeg? no — API call of a cloud model.

**IMAGE GENERATION** — `core/aicut/images.py`: image generation profiles (from
`images_profiles.json`), request validation, generation: `gen_image` → API call.

**SELF-CHECK** — `core/selfcheck.py`: Whisper over the stitched keep-audio — high
word confidence at a seam = normal, low = bad seam; errors — to log, into
`.cuts.json`.

**DRAFT RENDER** — `core/draftrender.py`: 720p without sound, preview before Premiere.

**SPEAKER PROFILES** — `core/speakers.py` + `speakers/*.json` — cut thresholds, style,
folder for a specific studio/voice. `.cut` — calibration thresholds for speaker A.

**AUDIO FADES** — `audio_fades` (style) — ~10ms fades at camera seams so it
doesn't click.

**RULES SUMMARY** — main rules are collected at the top of the document (see
"Rules"), the rest are by section. `core/verify_jsx.py` doesn't catch animation
errors — that requires a render in AE.

**Backlog** — see the end of the document.

**Open questions** — see the end of the document.

## Data contracts

**INTRODUCTION.** Contracts are what the system promises at the boundaries. Here:
the full list of files and JSON schemas, from `Reelsi_out/` to `ai_config.json`.

**FILES** — `Reelsi_out/`:
- `NN_stem.xml` — Premiere FCP7 xmeml (60fps).
- `NN_stem.project.json` — how to reassemble the XML (cameras, offsets, keep,
  edit memory).
- `NN_stem.cuts.json` — what and why was cut (AI decisions, nothing silently).
- `NN_stem.omni.json` — verbatim Omni transcript by intervals (cache).
- `NN_stem.draft.mp4` — draft render 720p.
- `NN_stem.review.json` — Omni review notes (EXPERIMENT, off).
- `<outdir>/_tmp/` — temporary files (cleaned by the 🧹 button and before a new
  cut).

**JSON schemas** — user data and user files (sidecars, `ai_config.json`, style and
speaker presets, Premiere XML (cut output), user XML, `.jsx`, `.srt`, `.drp`) are
written atomically (tmp + fsync + replace), see `core/fileio.py`; regenerable
temporary artifacts (transcript caches, `_tmp/` intervals, proxy cache) are written
directly — losing them costs nothing. One rule: atomic writing goes ONLY through
`core/fileio.py`, `os.replace` outside it must not appear anywhere in `core/` and `api/`
(guarded by `tests/test_infra_dedup.py`).

**`ai_config.json` contracts** — server-side, not localStorage:
- LLM profiles (LM Studio / Claude / OpenRouter / OpenAI-compatible),
- keys (mask on save = "not changed" → the key is kept),
- `active` — active profile (fallback),
- `step_profiles` — profile per step (cut/yellow/inserts/intro).

**`ui_state` contracts** — localStorage is primary, `/api/ui_state` →
`ui_state.json` is server-side mirror (survives browser change/clearing/quota);
interface state, NOT project:
- `speaker`, `style`, `subengine`, `selfcheck_model`, `media_vol`, `ins_slots`.

**`xml_state` contracts** — XML data (server-side, reassemblable):
- `ncams` (camera count by file), `offsets`, `cam_order`, `user_overrides`,
  `ins_rejected`.

**`ins_data` contracts** — insert state (per clip):
- `inserts` (list), `rejected`, `ins_image` (images), `ins_video` (videos),
- `ins_slots` (slots 1/2), `gen_image`, `gen_video` (generation flags).

**final voice contracts** — the clip's processed voice `<stem>.voice.<key8>.wav`
(next to the XML, `core/voicefx.py`) — the baked processed voice of camera 1: `key8`
  is the first 8 characters of the processing cache key (`final_voice_key`), so the
  name changes together with the settings: a new take lands NEXT TO the old one
  instead of on top of it. The version
  name and the key live in the `<stem>.voice.json` sidecar — `{"key": …, "src": …,
  "file": "<file name>"}` — and the sidecar is written atomically AFTER the file
  appears, so a reader never gets a dead path. Readers take the path ONLY through the
  `final_voice_path` resolver: the name from the sidecar when the file is there,
  otherwise the old `<stem>.voice.wav` (clips baked before versioned names keep
  reading as they did). Why a versioned name: under a constant one the track had to be
  REPLACED in place, and on Windows that replacement fails while the file is held open
  by the preview player (it plays exactly that file), by `/api/media` or by an open
  AE/Premiere project — the new voice never appeared at all and the failure was
  silent. The preview player plays the IMMUTABLE cache copy (`cache_path`: named after
  the settings), while the version file next to the XML is what AE
  (`final_voice_for_build`), Premiere XML (`core/xmlbuild.py`), `.drp` (`api/build.py`)
  and the draft render (`core/draftrender.py`) read; the other versions are pruned
  best-effort — a busy one is removed next time. One bake per clip: `ensure_final_voice`
  holds a lock on `realpath(xml)`, so a second order for the same clip gets the ready
  track without computing. A failed bake is visible in the build RESULT: a clip with
  processing on and no voice attached lands in `JOB["failed"]` as a record with
  `warn: true` and a reason (`api/build.py`, `_voice_warn`), and the interface shows it
  as a warning in the build window and in the final toast (`static/app/90-ae.js`), not
  as a log line.

**`px` contracts** — pixel contracts for inserts (coordinates, scale) — `px` in
`xml2ae` — `PIX_*` constants in `layout.py`.

**scene plan contracts** (`/api/scene` → `scene_plan`, the same maths as the build
but without roto and without writing; the preview reads it and never recomputes).
Fields added by tasks ZA–ZQ:
- `zoom.holds` — the per-key 0/1 list that replaced the single `zoom.hold`: a HOLD
  segment keeps the left value, a smooth one is interpolated (`keysAt(…, holds)`);
- `zoom.pan` — `[pan_x, pan_y]` in comp px and `zoom.rot` in degrees (frame offset
  and horizon, ZB); `zoom.fit` is always `100` after ZE, because the `cam1_fit`
  multiplier is already folded into the zoom KEYS in one place; `zoom.follow` —
  `{keys, ease}` of the head track (no key at all when the checkbox is off);
- `lumetri` — `None` or the nine values with the clip exposure already added;
- `intro_fsize` (the intro size BEFORE rows auto-shrink, ZL) and `intro_cam`
  (whether the intro rides the camera 1 null, ZM).

**`xml2ae` contracts** — the JS structures built by Python and read by the `.jsx`
in AE (tasks ZA–ZQ):
- `CAM1_SCALE = [[frame,percent],…]` — the camera 1 null zoom. A key may carry
  `[frame,percent,mode]` and `[frame,percent,mode,hold]`: `mode` is the incoming
  interpolation (`0` Easy Ease, `1` out 35, `2` in 90) and `hold` means the segment
  from this key to the next is HELD. `CAM1_HOLDS = [0|1,…]` has the same length and
  is what the `.jsx` uses to set HOLD/BEZIER per key side; keys without the fourth
  element keep the old rule (`_zoom_key_holds(keys, legacy_hold=…)`), and
  `_zoom_max` takes the same `holds` list.
- `insertlib.nobg_path` is ONE cache of a photo without background for the build and
  the preview: the `media` of a plate photo insert is swapped for `<stem>.nobg.png`
  in `scene_plan` (`_ins_js`) BEFORE the plate geometry is computed, so the AE build
  and the browser preview take the frame and the file from the same place (rembg
  trims empty margins, so the proportions of the `.nobg.png` differ from the source —
  task ZQ).
- Every font in the `.jsx` goes through `setFont(d, ps)`: it caches a working copy
  per PostScript name in `_FONT_PICK` (`_fontPick`), because AE may hold two records
  with the same PostScript name and silently keep the default font when the first
  one fails. No `.font =` assignment outside those two functions.

**`roto` contracts** — `roto/` in `Reelsi_out/` — RVM masks; `roto.json`
(metadata: who, when, model, model key). The head track is a sidecar next to the
XML (`<stem XML>.head.json`, task ZC).

**`emph` contracts** — the strength of highlighted words: the sidecar
`<stem>.emph.json` next to the XML (`core/emphasis.py`),
`{"key": …, "scores": {index: {"emo": …, "stress": …}}}`. The key holds `EMPH_VERSION`,
camera 1's source and its mtime, the set of scored words (index, start, end, text), the
set of highlights and the intro word times. A key mismatch means the sidecar is not read
at all (`valid=False`) and the zoom rule falls back to its previous behaviour: a word
added by hand after the calculation is not in the sidecar, and calling it "weak" silently
would be wrong. The word numbering follows the PLAN (`plan_words`: words after the intro
words are removed plus the intro words that continue the row), otherwise the calculation
and the reading would diverge on a clip with an intro. The zoom rule lives in
`plan["zoom"]["cam1"]["take"]["yellow"]` (and the camera-2 twin): `strong`,
`min_pct` (the percentile of the CLIP's highlight strengths), `max_per_piece`,
`second_min_s`, `scores`/`uncomputed`. A phrase is as strong as its strongest word, and
cycles are counted along the timeline rather than by strength, because the
"camera already at its peak" state must stay correct.

**`styles` contracts** — clip styles: `core/styles.py` + `styles/*.json` — style
preset.

**`ins_image` / `ins_video` contracts** — insert images/videos: `.png`/`.webp`,
`.mp4`/`.webm` — in `Reelsi_out/`, indexed by `insertlib`.

**`VJOB` contracts** — video generation: its own thread and state (not JOB):
`VJOB` (process), `VJOB_STATE`, `VJOB_ID`, `VJOB_CUR` (queue), `VJOB_CANCEL`.

**`JOB` contracts** — common job engine: `JOB` (process), `JOB_LOCK`,
`JOB_QUEUE` (queue), `JOB_CANCEL`.

**`emit` contracts** — log emitter: `emit(msg)` — a string into the UI log
(stream).

**`ERR_*` contracts** — error codes for translation: `core/umsg.py` +
`static/i18n/en.json`.

**`REELSI_*` contracts** — environment variables for `tools/webui_test.py` (port,
folder).

## Web UI (structure)

**GENERAL** — the only interface is `webui.py` (port 5001). Front — 
`templates/index.html` + `static/app.css` + `static/app/*.js` (ES modules? — no,
plain `<script>`). UI stack: pure JS, no frameworks. The translation dictionary
(`static/i18n/en.json`) is inlined into the page unconditionally (~300 KB per page
load, including Russian UI), because manual language selection in localStorage
overrides server default `ui_lang()`, and loading via fetch caused a race condition
with camera strings.

**FRONT STRUCTURE** — `static/app/`:
- `00-core.js` — localStorage migration to Reelsi keys, i18n, common helpers.
- `10-settings.js` — AI provider profiles, terminology dictionary, profanity and censor lists.
- `20-widgets.js` — SVG icons, number scrubbers, step switching.
- `30-video.js` — "Video" tab: video generation and task history.
- `40-queue.js` — project, cameras, clip queue, cutting job launch and polling.
- `50-chrome.js` — log window, modals, tooltip helpers.
- `55-progress.js` — the ONE progress form (header, bar, clip rows, buttons): the
  `PROGEV` dictionary (event code → human status), `PROGLOG` (log line → event code,
  one place for the whole interface), API `progOpen`/`progItem`/`progDone`. Every long
  operation goes through it; its markup lives nowhere else.
- `60-preview.js` — preview player, volume controls, word bar, intro markup.
- `70-editor.js` — cut editor (timeline) and step 2 markup.
- `80-inserts.js` — inserts modal and asset library: scanning, auto-matching, import.
- `87-roto-preview.js` — the "Compute roto and tracking" button in the AE preview: it
  calls `/api/preview_calc` (the same precompute as the build, `core/xml2ae/precompute.py`),
  shows the progress row inside the frame and marks "computed" once the shared cache holds
  the masks. The button is visible only on the Intro tab and only when the clip style has
  roto or head follow enabled.
- `85-inserts-view.js` — insert preview: virtual player and timeline. The camera 1
  frame is drawn through ONE matrix, `ipvCamMatrix` (`screen = C + S·(R·p − C_c) + T`
  from `zoom.keys`, `zoom.rot` and `ipvCamShift` = `pan` + head follow), the whole
  source is drawn with `drawImage(v, 0, 0, vw, vh, …)`; `ipvCamChild` is only for
  children of the camera 1 null (its inserts, the intro, the shade), free camera 2
  inserts get no shift; `ipvLumetriFilter` builds the approximate `plan.lumetri`
  filter (the real Lumetri formulas are closed). Camera 2 uses its own
  `plan.lumetri2` filter only while its link chain is open (`lm2_link=False`);
  with the chain closed the plan carries no `lumetri2` and camera 2 takes camera 1's
  filter.
- `86-lut.js` — the camera LUT from the speaker profile: the `.cube` table comes from
  `/api/lut` and is applied to the preview frame on the fly in a hidden WebGL2 canvas
  (`lutApply`, one context per preview, the 3D texture is rebuilt only when the table
  changes); without WebGL2 the preview runs without a LUT.
- `87-camframe.js` — the camera frame: which part of the source goes into the reel frame
  (`frame` in the speaker profile, per camera). Editing mode in the step 3 preview
  (the whole source fitted, the reel-aspect rectangle on top, drag to shift, wheel to
  zoom, "Save to speaker" through `/api/savespeaker`); the numbers are a mirror of
  `core/frame.py` (the single source of the clamp, checked by `tests/test_cam_frame.py`).
- `88-cams.js` — camera layout editor and CPV mini-player.
- `90-ae.js` — After Effects step: words, intro, manual inserts.
- `94-stylepanel.js` — style panel (Effect Controls) built from the `/api/style_schema`
  answer; loads before `95-styles.js`.
- `95-styles.js` — style presets, speaker profiles, color pickers, music, ASR engines.
- `99-boot.js` — state persistence and UI boot.

**BACKEND STRUCTURE** — `api/` (Blueprint):
- `_core.py` — JOB/LOCK/emit, common.
- `jobs.py` — cut/markup jobs.
- `files.py` — files, scanning.
- `presets.py` — presets.
- `ai.py` — AI config, test, models.
- `editor.py` — editor.
- `build.py` — AE assembly.
- `inserts.py` — inserts.
- `videogen.py` — video generation.

**KEY MECHANISMS**:
- **`/api/status`** — incremental polling (since=N) of events.
- **`/api/omnicut_run`** — cut job (subprocess).
- **`/api/build_run`** — AE assembly (`.jsx`).
- **`/api/render_run`** — headless AE render (includes the preflight check).

## AI providers: profiles (LM Studio / Claude / OpenRouter / OpenAI-compatible)

`aicut` (a package since 2026-08-06) — the single entry point for LLM calls:
profiles (LM Studio / Claude / OpenRouter / OpenAI-compatible), keys, models,
reasoning.

**PROFILES** — `ai_config.json`:
- **LM Studio** — local OpenAI-compatible server (default) — nothing leaves the
  machine.
- **Claude** — cloud profile, Anthropic API.
- **OpenRouter** — cloud profile, model aggregator.
- **OpenAI-compatible** — any compatible endpoint.

**KEYS** — stored in `ai_config.json` (mask on save = "not changed").

**MODELS** — `aicut` supports: LM Studio (any), Claude (sonnet/opus),
OpenRouter (any), OpenAI-compatible (any).

**REASONING** — `reasoning_steps` — the "thinking" level per step
(cut/yellow/inserts/intro): off/low/medium/high.

**STREAM-CANCEL** — `aicut.llm.begin_call` — token stream, cancel via
`aicut.llm.cancel_stream` (generation flag).

**STEP PROFILE** — `step_profiles` — profile per step (cut/yellow/inserts/intro),
empty = common `active`.

**GENERATION** — `core/aicut/images.py` (gen_image), `core/aicut/video.py` (gen_video) —
generation profiles (separate from LLM), models from the catalog.

**FALLBACK** — `resolve_profile` = active profile if a step has none of its own.

## Image and video generation

**IMAGES** — `core/aicut/images.py`:
- generation profiles (from `images_profiles.json`),
- `build_image_prompt` — prompt assembly (no "technical tail").
- `gen_image` → API call (cloud model, e.g. Nano Banana).
- caption: appended with a space (not a comma) — "broken eyeglasses 3d icon".
- rembg (background removal) — `image_rembg_on`.

**VIDEO** — `core/aicut/video.py`:
- video model catalog (`video_catalog.json`),
- request validation (prompt, duration, forbidden tags),
- `gen_video` → API call of a cloud model.
- `normalize_video_tags`, `resolve_refs` (references in videos),
  `video_warnings`.

**SLOTS** — slots "1"/"2" — TWO GENERATIONS of ONE subject, slot `a` — batch.

## API endpoints (summary)

**GENERAL** — all `/api/*` are JSON, subprocess jobs stream the log via `emit`.

**JOBS** (`api/jobs.py`):
- `POST /api/omnicut_run` — AI cutting (subprocess).
- `POST /api/run` — classic cutting.
- `POST /api/draft_render` — draft render.
- `POST /api/status` — incremental log polling (`since=N`).
- `POST /api/cancel` — cancel a job.
- `GET /api/clean_tmp`, `GET /api/tmp_info` — `_tmp` cleanup / info.

**FILES** (`api/files.py`):
- `POST /api/files` — camera/file lists.
- `POST /api/pickmedia` / `pickfiles` / `pickone` / `pickaudio` / `pickdir` — native
  file dialogs (one-shot, no server-side persistence).
- `GET /api/media` — serve any media file (Range for streaming); `?nobg=1` serves
  `insertlib.nobg_path` for an image (the same `<stem>.nobg.png` cache the build
  uses), video is served as is.
- `GET /api/waveform` — peak cache for the cut editor.
- `POST /api/cams`, `/api/cammatch` — camera layout, audio-based match of cam 2..N.
- `POST /api/newtakes` — "already cut" filter for the queue.
- `GET /api/music_random` — random track from the music folder.
- `GET/POST /api/ui_state` — server-side mirror of UI state.

**AI** (`api/ai.py`):
- `GET/POST /api/ai_config` — provider profiles/keys (masked).
- `POST /api/ai_models` — provider model list.
- `POST /api/ai_test` — mini LLM call (check).
- `POST /api/ai_yellow` / `ai_inserts` / `ai_intro` — markup; `ai_stop` cancels.
- `POST /api/ai_genimage` — image generation; `POST /api/rembg` — background removal.

**EDITOR** (`api/editor.py`):
- `POST /api/editor_load` / `editor_save` — cut editor state, edit memory
  (restored/deleted).
- `POST /api/edit_word` — word text edit; `set_yellow` / `clear_subs` — subtitle tags.
- `GET /api/words`, `POST /api/xml_state`, `GET /api/omnicut_cuts` — word list/state.
- `POST /api/breaths` — sigh cuts from `.breaths.json`.
- `POST /api/asr_engines` — speech engine registry.
- `POST /api/gen_subs`, `POST /api/aicut_preview`, `POST /api/scanxml` — subtitles
  from scratch, virtual EDL for the preview, folder scan.

**INSERTS** (`api/inserts.py`):
- `POST /api/insertlib_info` / `scan` / `reject` / `match` / `import` / `describe`
  / `desc` / `items` — B-roll library index, matching and description.

**STYLE / SPEAKERS** (`api/presets.py`):
- `POST /api/styles` / `savestyle` / `delstyle` — clip style presets.
- `POST /api/speakers` / `savespeaker` / `delspeaker` — speaker profiles.
- `POST /api/terms` — ASR term dictionary; `POST /api/censor_words` — censor lists.

**ASSEMBLY** (`api/build.py`, `api/render.py`, `core/render_job.py`, `api/previewproxy.py`):
- `POST /api/build_run` — AE assembly (`.jsx`).
- `POST /api/scene` — scene plan for the step-3 preview (all assembly math without
  roto/`.jsx`); the preview draws it and never recomputes.
- `POST /api/cams_load` / `cams_save` / `swap_cam` — camera layout on step 2.
- `POST /api/export_xml` / `export_drp` — download the timeline as XML / `.drp`.
  **`export_xml` is a POST, not a GET**: it rewrites the XML (`sync_xml_voice`), so as a GET it
  could be triggered by another page with an `<img src>` tag. The front end (the clip download
  button, the step-2 download, the XML format in the download dialog) posts the path in the body.
- `POST /api/render_run`, `GET /api/render_status` — headless AE render (Windows
  only, `aerender`), own job.
- `POST /api/preview_proxy`, `GET /api/preview_proxy_status` — 720p 4:2:0 proxies
  for 4:2:2 10-bit sources the browser can't decode, own `PXJOB`.
- `POST /api/preview_calc`, `GET /api/preview_calc_status`,
  `POST /api/preview_calc_cancel` — roto and tracking calculation from the preview
  button (`api/previewcalc.py`): the same `core/xml2ae/precompute.py` the build uses,
  the shared job lock, its own `PCJOB`. The first request answers "what is already
  computed" without touching the GPU.

**GDRIVE** (`api/gdrive.py`):
- `POST /api/gdrive_download`, `GET /api/gdrive_status` — material from a Google
  Drive link via `rclone`, own job (does not take the cutting JOB).

**VIDEO** (`api/videogen.py`):
- `POST /api/video_gen` — start generation; `POST /api/video_cancel` — cancel.
- `GET /api/video_status` / `video_history` — status / history.
- `POST /api/video_models`, `POST /api/video_probe` — model list, file probe.

**SYSTEM**:
- `POST /api/cancel` — cancel a job.
- `GET /api/status` — incremental polling (since=N).
- `GET /api/clean_tmp` — `_tmp` cleanup.

## Backlog

- **Intro above or below roto? (unresolved, 2026-07-27)** — verify on a FRESH
  assembly.
- `geologica` style — structural spaces (see `docs/ROADMAP.md`).
- 1-camera projects — all photo inserts are "Cam 1" (exit from behind, roto
  needed).
- Sound trimming (pop/transition) with sliders — planned.

## Gotchas

**GOTCHA 0b — how many clips the card cuts is decided by free VRAM, not by the wish
(2026-10-07).** Every cut process holds a CUDA context of its OWN, ~568 MiB, until it exits
(measured on a 4096 MiB card: four processes = 2307 MiB, peak 696 MiB under work), so the clip
count taken from the settings (1..16) is capped by free memory:
`core/device.py:parallel_width_budget` = `free_vram // CUT_ROLE_VRAM_MIB` (600 MiB), measured
the way `doctor.py` does (torch, then `nvidia-smi`); a machine without CUDA has no cap, and the
reason goes into the job log ("карта 4.0 ГиБ, каждый ролик держит ~600 МиБ — одновременно не
больше 6 (просили 10)"). Ten clips on a 4 GB card used to mean `OutOfMemoryError` inside a
subprocess and a bare "code 1". Same place: **subtitles (`POST /api/gen_subs`) recognize under
`gpu_lock("субтитры")`** — without the lock two ASR models (Whisper and GigaAM) sat on the card
next to a running cut — and a model that does not fit is retried with `compute_type="int8_float16"`
and then one step down the ladder (large-v3 → medium → small) with the reason in the log; when
nothing fits, the answer carries the `whisper_gpu_fallback` code with its translation.
Out-of-memory is recognised by exception type or text in one place
(`core/device.py:is_oom_error`). Guard: `tests/test_gpu_budget.py`.

**GOTCHA 0a — a preview shadow is a Drop Shadow → CSS translation, not a "similar number"
(2026-10-07).** The preview drew shadows with hard-coded numbers of its own (`soft/2` for the
intro precomp, its own `text-shadow`s for the subtitles, `drop-shadow(0 6px 18px …)` for the
inserts, nothing at all for the plate), and the shadow came out 1.34× wider than the one AE
builds. A render of AE 2026 measured the translation (a white square in a precomp, black
shadow, Opacity 255, Distance 0; Softness 50/150/287 gave a Gaussian sigma of 9.5/28.0/53.5 px
by an erf edge fit, rmse < 0.004), so σ = 0.187·Softness; CSS blurs a `drop-shadow` with
σ = r/2, hence `r = 0.374·Softness`. The translation is ONE door for the whole preview,
`aeShadowCss(sh, k)` (`static/app/85-inserts-view.js`): `k` is preview pixels per frame pixel,
the offset is `dx = Dist·cos(Dir)`, `dy = Dist·sin(Dir)` (frame Y points down) and
`alpha = Opacity/255` one to one. Subtitles (the filter sits on the word container — it is the
Drop Shadow of the subtitle precomp layer, so it covers the word background too), the subtitle
background, photo inserts (the filter is composed with the entry blur in AE's order) and the
intro precomp all call it. The numbers of the three shadows live in one place in Python
(`core/xml2ae/plan_decor.py:shadows_plan` — the `SH_SUB_*`, `INS_SH_*` and `SUB_BG_SH_*`
constants from `core/xml2ae/layout.py`) and reach the preview as `plan["shadows"]` (`sub`,
`ins`, `sub_bg`; `op255` is AE's 0..255 scale, into which the knob's percent is converted by
multiplying by 255/100), while the `.jsx` gets them through the same substitutions; the preview
keeps no shadow numbers of its own. For a default style the built `.jsx` is byte-for-byte the
old one. Stand: `tests/test_shadow_css.py`.

**GOTCHA 0 — two doors to one state are a bug even when both "work" (2026-10-02).** An
insert on step 3 lives in two places: the PREVIEW list and the clip card (what is saved).
Dragging or stretching it on the timeline edited only the preview list, which was rebuilt
from the cards on the next opening — the timing edit disappeared silently. The same
happened with camera 2's zoom (two copies of "which keys") and with intro row glow (the
row's `fx` field against the style switch `intro_accent_glow`). The rule: one value has ONE
source; the second door must write where the first one lives (a shared card-lookup helper,
as the X/Y shift has), and a "saved but not read" field is better removed. Test the value
ARRIVING in the state and surviving a reopening, not that a function was called.

**GOTCHA 1 — GigaAM-Emo takes a PATH, and an array only through `forward` (2026-10-03).**
`get_probs` of the `emo` head runs `prepare_wav` → `load_audio` → ffmpeg and therefore
expects a string: calling it with an already read array raises
`TypeError: expected str, bytes or os.PathLike object, not ndarray` (caught in a live run).
Pushing the window through a temporary file is no good either — there is one window per
highlighted word (dozens). So `core/emphasis.py:emotion_probs` repeats the steps of
`get_probs` over the array: mono float32 → 16 kHz (`EMO_SR`) → a tensor on the model's
`_device`/`_dtype` → `forward` → `avg_pool1d` over time → `head` → softmax → `id2name`.
The error only shows up in a live run — a stubbed model in a test does not reproduce it.

**GOTCHA 2 — AE merges zoom keys on one frame, and the HOLD/ease flags shift (2026-10-02).**
A cut jump and the start of a highlight zoom in one and the same frame produced TWO keys in
one frame; AE merges them and moves the ease/HOLD flags onto the neighbouring key, so the
zoom became a jump ("it snaps") and camera 2 revealed its frame edge by up to 169 px. Such
keys are merged into one in the plan, the `.jsx`, the preview and the tracking — by ONE
rule. Next to it: the shift clamp must compute the scale along AE's CURVE and take the
SMALLEST scale on the segment (a straight line missed), and the tracking samples must be
keyed at EVERY sample (10/s), linearly, in batches: thinning with a 4 px tolerance plus
Easy Ease on every key pushed the line off the frame and made the camera "brake" at the
keys. Only AE (frame by frame) can measure this; tests over the Python geometry cannot.

**GOTCHA 3 — the UI geometry tests run in Chrome, not by eye (2026-10-02).** The layout of
the "Inserts" window and of the step-3 preview is checked in a real browser: one window
height with 2 and with 30 inserts, no page scroll, the timeline inside the window, and the
step-3 layout compared against a CSS stand with a ±2 px tolerance. Reason: frame defects
(the window grew with the list, a hidden column of cards leaked into the step-3 preview)
are invisible in jsdom — a JS stand without a layout engine misses them entirely.

**GOTCHA 4 — a precomp layer and a shape layer scale differently (task FE,
2026-08-25).** The AE formula: `screen = Position + (P_local − Anchor) * Scale`. For a
PRECOMP layer local coordinates match the comp pixels, so the anchor `[W/2, POSY]`
puts the scaling center exactly at the subtitle line. For a SHAPE layer (subtitle
plate, `bgLayer`) the content is drawn around local `(0,0)`, and the same anchor
carries the plate to `(162, 343)` — the top-left corner of the frame. For a shape
layer the anchor is computed as `[0, POSY − bg_y]` (`bg_y` = the plate's original
`Position.y`), position `[W/2, POSY]`. Test the RESULT by the screen formula (where the
center lands), not the fact that `setValue` was called: the test that checked the call
fact missed the defect. Same place: preview and AE must match — in the preview the
plate lives INSIDE `#ipvsub` and rides the shared `transform: scale()`, so a wrong
anchor in `.jsx` gave a "preview says one thing, AE another" divergence.

**GOTCHA 5 — XML files with captions.** `core/xmlbuild.py` has duplicate lines —
traces of past edits (the code works but reads hard). Don't touch without need.

**GOTCHA 6 — AE rendering.** `core/verify_jsx.py` doesn't catch animation errors.

**GOTCHA 7 — GigaAM and VRAM.** GigaAM and 27b can't live in VRAM simultaneously
(16 GB) — the "holds/unloads" scheme (see above).

**GOTCHA 4 — UI state.** localStorage is primary, `/api/ui_state` →
`ui_state.json` is the server-side mirror that survives browser change, cache
clearing, and quota limits (see `tools/webui_test.py`).

**GOTCHA 5 — `insertlib`.** Indexes only `Reelsi_out/`.

**GOTCHA 6 — GigaAM runs in subprocesses.** Transcription and subtitles
(`core/gigaam_subs.py`) run in subprocesses so OOM doesn't kill Flask.

**GOTCHA 7 — `xml2ae` and layer order.** Depends hard on the template order —
don't change without reassembly.

**GOTCHA 8 — `transcribe_words_whole`.** Not `transcribe_whole`.

**GOTCHA 9 — `pyannote`.** Removed 2026-08-10 (gated weights + Windows bugs in
pyannote/speechbrain); longform is windowed (~18s) with seams at the quietest point.

**GOTCHA 10 — `core/vad.py`.** Energy VAD (25ms/10ms), threshold = floor + 18dB.

**GOTCHA 11 — `.jpg` inserts.** AE can't read them (to check).

### Security (closed 2026-10-07)

The rule in one line: **names and addresses from an EXTERNAL service's answer are untrusted
input**, exactly like a request body. One helper for all of it — `core/_pathguard.py`:
`safe_name()` cleans a name down to letters, digits, `.`, `_` and `-` (separators, a bare `..`
and control characters are replaced — `../x` becomes `-x` rather than a way out of the folder;
Windows-reserved names such as `CON` are dropped too), and `inside_dir()` additionally checks
the `realpath` of the result stays inside the target folder (cleaning the name alone does not
catch a symlink inside the folder). A stock item's file name, built from `cand["id"]`, used to
let `id="../../../../tmp/OWNED"` land the file outside the library.

Addresses go through `core/app_meta.unsafe_url_reason(url)`: plain `http`/`https` only, and
EVERY address a host name resolves to is checked (a single host string is bypassed by a name
that resolves to a private IP): loopback, link-local, private, reserved, multicast and
undefined are refused. Redirects run through `SafeRedirectHandler` with the same check, and a
request to a foreign host carries no `Authorization`, `Proxy-Authorization` or `Cookie`
(`HTTPRedirectHandler` otherwise moves every header except `Content-*`). A refusal is a reason
not to download, not a crash: the loop takes the next address and the paid-for result is not
lost. The guard sits in `core/stock.py`, `core/aicut/images.py` and `/api/video_probe`;
requests to the PROVIDER itself are not affected — a person picks its `base_url`, and
`http://127.0.0.1:1234` (LM Studio) is a normal address there. Guard test:
`tests/test_security_fixes.py`.

- **AI profile headers go out masked, like the key.** An `Authorization` / `x-api-key` value
  is as much a secret as `api_key`, and `GET /api/ai_config` handed it to the browser in clear
  text (localStorage, devtools, the "Custom headers" field itself). `apply_profile_headers`
  reads the headers from the profile, they go out as `•••xxxx`, a value that is still a mask
  never overwrites the real one on save (the same `resolve_header_mask` rule as the key), and a
  live call (`Check`, model list) resolves the mask back. `env:VAR` is not masked: it is a
  variable name, not a secret.
- **The GPU lock no longer spins on "waiting" forever.** `core/gpulock.py` treated ANY failure
  — a read-only folder, missing permission, an `msvcrt` failure — as "busy": `while True` with
  a 0.5 s sleep and a "waiting for the GPU" line, with nothing to wait for. Being busy is now
  only a refusal to lock an ALREADY OPEN file (some other process holds it, and waiting for
  that is honest), while a lock file that cannot be created or opened raises `ReelsiError` with
  the reason at once.
- **`_host_is_local` compares the PORT too.** `127.0.0.1:5999` is a local name but a foreign
  door on this machine: a `Host` that names a port must name OUR port (`api/_core.py`). The
  address for a render without AE is built from the server's own port instead of the `Host`
  header, which anything can set; the port comes from `local_ui_port()`, not from the request's
  `SERVER_PORT` (the HTTP server builds that from the same header).
- **`/api/export_xml` is a `POST`** because it writes a file: as a `GET` a hostile page could
  trigger it with an `<img src>` tag.
- The link to `.github/SECURITY.md` was fixed — it pointed nowhere.

## How to run / check

- Web: `python reelsi/webui.py` → http://127.0.0.1:5001.
- AI cut CLI: `python reelsi/omni_cut.py --cam1 A.MP4 --cam2 B.MP4 --out cut.xml
  --mode gigaam`.
- Classic CLI: `python reelsi/reelsi.py --cams 2|1` (or `--single`, `--no-cut`,
  `--aggressive`).
- Tests: `python -m pytest tests -q`.
- **As in CI: `python -m pytest tests -q -n auto --dist loadgroup -m "not perf"`.**
  `-n auto` (pytest-xdist) spreads the suite over workers; `--dist loadgroup` is
  mandatory with it and keeps the tests that start a REAL Chrome (`test_ui_static`,
  `test_wv_ins_modal_geometry`, `test_webrender`, marked `xdist_group("chrome")`)
  inside one worker, because several browsers at once measure geometry unreliably;
  `-m "not perf"` leaves out the time-budget tests (`test_emphasis_prosody`) that
  measure the runner rather than the code and run locally. The pair
  "`-n auto` + `--dist loadgroup`" is guarded by `tests/test_ci_config.py`.

**PROJECT RULES** — comments and commits are IN RUSSIAN. Chat too.
**PROJECT RULES** — the repository is the `reelsi/` folder, branch `main`.
**PROJECT RULES** — before committing — `python -m pytest tests -q`.

## See also

- `README.md` — overview, run.
- `docs/CUTTING_SPEC.md` — the full cutting spec (both engines).
- `docs/HIGHLIGHT_SPEC.md`, `docs/INSERTS_SPEC.md`, `docs/INTRO_SPEC.md` — markup specs.
- `docs/ROADMAP.md` — open directions (geologica, video-use stages) and decisions history.
- `docs/DRP_SPEC.md` — DaVinci Resolve export spec.
- `docs/DESIGN.md` — UI style guideline.

