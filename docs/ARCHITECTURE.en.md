# Reelsi — architecture, how things work, roadmap

> **Note**: a full translation of [ARCHITECTURE.md](ARCHITECTURE.md) (in Russian), which stays the source of truth; if anything diverges, the Russian version takes precedence.

## UI contract of the batch wizard

The UI is organized around a three-step batch pass: cutting, markup and After
Effects assembly. Opening a single clip in the preview is optional and serves a
spot check. On the AE step the checkboxes define the batch (an empty selection
means the whole set), and a sticky panel after `#clips3` starts assembly or render
for the same selection. The JSX mode choice and the "Current only" action remain
local accelerators and do not change the batch semantics.

Google Drive import is the first but collapsed block of step 1; its `gdrive_*` ids
are part of the UI contract. The speaker is stored in `clip.job.speaker` and edited
by a compact expandable control in the AE clip row.

> Read THIS file first in a new session. It holds the big picture, data contracts
> and gotchas. Point specs live in `README.md`, `docs/ROADMAP.md`,
> `docs/HIGHLIGHT_SPEC.md`, `docs/INSERTS_SPEC.md`, `docs/INTRO_SPEC.md`.
> The English version of this file is `docs/ARCHITECTURE.en.md`.

## What it is and where we're going

An AI editor working on top of **Adobe Premiere + After Effects**.
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
creative end stays manual — fine-tuning in Premiere; from cameras to the AE render
(including the headless render without opening After Effects) everything is
automated.

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
4. **Reelsi · "Markup" tab** → `core/aicut/` (LLM): highlight words, inserts
   (by library or by AI generation of image/video), intro.
5. **Premiere — final fine-tuning** (manual, optional; editor edits are remembered
   in `user_overrides` and respected on re-cut).
6. **Reelsi · "After Effects" tab** → first the **scene plan** (`/api/scene`,
   all assembly math without `.jsx` and roto), which the step-3 preview draws in the
   browser and edits by dragging; then
   `xml2ae + styles + roto (RVM)` → `.jsx` from the approved plan.
7. **Render** — headless: the "Render" button assembles the project and drives
   `aerender` (Windows only, engine — `core/aerender.py`); AE does not have to be
   opened by hand for this.

Only stage 5 (and even that optionally) remains manual — the creative end. The visit
to After Effects is gone from the pipeline: only the render delivery is left there.

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

## Model service (`core/model_service.py`)

A long-lived process, ONE per machine, that keeps the weights of the local models in
memory and counts with them on request. Before it, every cut clip was its own process:
its own `import torch`, its own CUDA context, its own read of the weights under the
`.gpu` file lock — and an unload right after the clip. The benchmark
(`tools/bench_gpu_cut.py`) measured ten 120-second clips at 147 s in total, with a median
55 s of waiting for the lock against 3–6 s of counting and 4.6–7.2 s of reading the
weights: the lock cost more than it saved.

**What it holds.** GigaAM heads (`transcribe_loaded` from `core/gigaam_cut/asr.py`),
the CED-tiny breath detector (`ced_probs` in `core/breath.py`) and the `emo` head for
phrase emotion (`emotion_probs` in `core/emphasis.py`). There is no counting logic in
the service: it calls the same functions as the local path, so the numbers through the
service and locally agree to the last bit. Silero VAD stays in the clip process: it is
ONNX on the CPU and takes no card.

**Slots, not a file lock.** A clip takes no `.gpu` lock for recognition or breaths:
instead of a lock the service counts **slots** — how many jobs run at once. On a card
they are sized from free VRAM (`free // (512 MiB of weights + 1 GiB of headroom)`,
1..4), on the CPU from half the logical cores (1..4). A busy card is not a refusal: the
service WAITS for free VRAM and says so in the log. Other heavy stages (RoFormer,
subtitle Whisper, render) keep the file lock.

**Idle and the fallback path.** The service exits after an idle timeout
(`REELSI_MODEL_SERVICE_IDLE`, 300 s) and returns VRAM; `unload()` clears the weights but
keeps the process, `shutdown()` stops it entirely. It starts lazily on the first
request: address, `authkey` and the start claim live in one file next to the job lock
(`<job.lock>.modelsvc.json`, owner-only permissions). A second client connects to the
living service and does NOT spawn a second one. If the service is missing or refuses,
cutting counts locally under `gpu_lock` and writes the reason into the log.

**Code version in the address file.** The address file carries a fingerprint (`build`)
— the hash of the content of `core/model_service.py` and of the modules it loads weights
with (`breath`, `emphasis`, `gigaam_cut.asr`, `gigaam_cache`, `device`) — and the device
(`device`). A client compares its own values with the recorded ones: if they differ, the
old service is stopped and a new one is started. Without it a code change would not
reach a machine where the service is already running, and a change of "where models are
computed" would wait for the idle timeout. `webui.main` does the same at server start
(`model_service.drop_stale`) without starting a new service. Why a content hash rather
than `APP_VERSION` or mtime: the app version is one number for the whole project and does
not change when a recognition window is edited, while mtime lies on `git checkout` and
archive extraction — it would stop a living service for nothing.

**Device choice.** The setting "Where models are computed" (⚙ → "Cut": auto / video card /
processor) lives in `ai_config.json` (`model_device`, action `set_model_device`) — a
machine setting like the video codec, and not in localStorage, because the service is a
SEPARATE process. `core/device.model_device()` reads it, and "auto" is resolved by
`pick_device` (cuda → mps → cpu). The processor is for machines without a card and for
those giving the card to a local LLM (LM Studio, Ollama).

**Unloaded for renders.** A render needs no models but needs the whole video memory:
`run_render_job` in `core/render_job.py` calls `model_service.shutdown()` before starting
a render (both the AE and the built-in engine). A failed unload is a line in the render
log, not a render failure.

**The ceiling in the UI.** `GET /api/model_cap` (`api/model_svc.py`) returns the device,
the slot count and whether the mode is "auto" — computed by the same code as the service
(`model_service.slot_count`). The route does NOT start the service and does not import
`torch` (free VRAM comes from `nvidia-smi`): the hint under "Clips at once" costs neither
minutes of weight loading nor an occupied card. The rule is guarded by a subprocess test
(`sys.modules`).

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
  If the model dropped the cluster ENTIRELY (the quotes mode does that — it honestly
  lists all three takes), the last take is RETURNED: on C1355 otherwise the whole
  story "ещё пять лет когда я ездил в штаты выступать" disappeared. We do not
  return only blocks with profanity/NG markers (`NG_MARKERS`) — there the model is
  right. The clean take is extended by `_take_tail` to the PAUSE, not to the cluster
  boundary: the boundary is merely where the match with the abandoned take ended
  ("используем его уже" matched, "очень давно" no longer did);
- `veto_unique_drops` — at the edges of every dropped range it rewinds words that
  belong to no cluster and returns such a tail if it is long (>=5 words and
  >=1.5s). Real garbage ends with a re-shoot, so it lies in a cluster; unique
  coherent speech is not a duplicate, it must not be cut;
- `heal_fragments` — 27b kept the beginning of a phrase and cut a short
  continuation ("и список" without "самых опасных", "а с" without "максимальным"):
  if a SHORT (`< HEAL_SEG`) kept piece runs into a short dropped tail that is in
  no cluster, we return the tail. Parasites from `FILLERS` are not brought back:
  the model drops them correctly;
- `dedupe_repeats` / `dedupe_fragments` / `drop_truncated` — finishing the
  leftovers (a neighboring repeat → the EARLY copy; a fragment not adjacent → the
  LATE copy, the text does not change; a word-prefix of the next one);
- **enumeration gate `align.is_enumeration`** (2026-08-04, complaint "an enumeration
  gets cut out"): "где он сделал вот это, А где он сделал другое" — the beginning
  repeats verbatim, and `find_takes`/`dedupe_fragments` saw a false start there,
  wiping everything up to the second occurrence. An enumeration is recognized by TWO
  signs at once: before the second take there is a conjunction link (`ENUM_LINKS`) AND
  the first one has its OWN meaningful tail (not a parasite and not a stub of a word
  that is about to sound in full). The candidate is moved LEFT before the check: the
  detector latches onto the middle of the repeat ("он сделал" instead of "где он
  сделал"), and the conjunction would otherwise end up inside the matched piece.
  The gate is shared by all paths — `align.find_restarts` and the SSM text gate
  (`ssm.text_has_repeat`) use it too, and the rule is duplicated in the cutting
  prompts;
- `drop_micro_keeps` — a ONE-WORD piece shorter than `MIN_KEEP` = 0.6s (two words
  in a row are already content: "во вторых" takes 0.56s) and any piece shorter than
  `MIN_ISLAND` = 0.35s ("а с" = 0.20s — garbage with any number of words).

`NG_MARKERS` work ONLY in return paths: the code never resurrects profanity and NG
lines that the model dropped, but it also does not cut profanity the model kept
(the hero swears for a reason — that is content). The post-pass cannot cut by
dictionary at all, in principle.

In markup mode the code does NOT replay the model's decision (invariant 2026-09-08):
the post-pass over the LLM answer is enabled only by the "Code cut editing" stage
(key `dedupe`, off by default); with the stage off `postprocess` itself switches to
light mode and does a single cleanup — `drop_micro_keeps` (micro-islands of 2-3
frames). Why: a measurement on five videos 2026-09-08 — the model gave 18 correct
cuts, the code added 6 of its own and all 6 turned out to be wrong (`force_takes`
and `dedupe_repeats` cut enumerations and role-play speech "это же укольчик",
"да я привык мне нормально на прогресс не влияет"), and `veto_unique_drops` brought
back correctly cut off-topic at the end of the video.

**Audio cut refinement (`refine_keep`, 2026-07-22).** Everything above works with
TEXT, while the complaints were about sound: "there are places left without sound and
with sighs" and "words get cut a little". A measurement on C1353/1355/1356 confirmed
both: inside the kept pieces 6-8s of silence accumulated (pauses shorter than
`SILENCE_SEC` do not pass the threshold), and cuts landing INSIDE a word numbered
28-30 per clip — CTC gives the boundary with ~40ms accuracy. That is why after the
intervals are built a pass over the wav itself follows:

- **boundaries by sound, not by a fixed padding**: from the edge of a word we go to
  the END OF THE WAVE within `EDGE_IN_MAX`/`EDGE_OUT_MAX`. Measurement: sound
  stretches after the "end of the word" according to CTC in 62-83% of words (median
  80-120ms, up to 400ms), so fixed 100ms is both "padding everywhere" and chopped
  tails. Now "выступать" gets 25ms of extra, while "давно" gets 211ms. The end of
  the wave = QUIET, `QUIET_RUN` quiet frames in a row (2026-07-28): on the very
  first quiet frame the cut landed in a dip INSIDE the wave — the transition before
  "п/т/к", the junction of syllables — and the word sounded chopped. The same
  `QUIET_RUN` measures word tails in the speech mask, otherwise the added wave tail
  is treated by the mask as a hole and the second stage cuts it back, in the middle
  of the decay. There may be no quiet at all (the next word starts immediately — a
  dropped duplicate) — then, as before, we land on the QUIETEST place: otherwise
  the cut lands on the attack of someone else's word and its stump is audible. A
  word belongs to a piece only if its MIDDLE is inside: by touch, a neighboring word
  the user deliberately cut was pulled in (up to 600ms);
- **the start of a piece is from the ENTRY INTO THE WAVE, not from the CTC start**
  (`_start_edge`, 2026-07-31). Complaint: "the start sometimes cuts not at full
  silence, I fix a lot by hand". The C1414 analysis showed the root: the CTC start of
  a word drifts relative to the sound by ±150ms in BOTH directions. For "я" it was
  160ms late — the `EDGE_IN_MAX` window ran into a continuous wave, there is no
  quiet, the fallback "quietest frame" landed on the attack of the word; for
  "транболлона" it was 130ms early — at that time an inhale was noisy there, and the
  cut landed on the inhale, although the real silence was AFTER the CTC start.
  Therefore the entry of a word is searched by sound: the voice is louder than
  `ONSET_DB` above the floor and not quieter than the peak of the word minus
  `ONSET_FALL` (inhale/room noise on real clips is 12-18 dB, speech 30-45), a quiet
  attack is brought back up to `ATTACK_MAX`, we step back by `START_PAD`. Comparison
  with the user's manual fine-tuning (8 clips, 179 pieces): the median difference
  from his boundary 60 -> 20ms, starts that landed on sound 19 -> 7, and in 6 of the
  7 remaining he cuts by sound himself (continuous speech, there is no silence
  there). The number of pieces and the content do not change — only the extra air
  goes away (~1s per clip). We did NOT switch the SPEECH MASK (below) to wave entry:
  there the price of an error is higher — on the same clips the wave-based mask cut
  the beginnings of quiet words ("клеточная", "поэтому") and they went into the hole
  as an inhale;
- **holes without speech**: inhales, "кхе", smacks and knocks are NOT transcribed by
  GigaAM, and that is what gives them away — there is sound, but no words on it. The
  speech mask is built from words with a `WORD_PAD` margin, everything outside it
  longer than `HOLE_MIN` is cut, and `HOLE_AIR` of air is left at the edges.
  IMPORTANT: the mask is expanded by SOUND, not by hard padding — otherwise a
  stretching letter ("давно" still sounds 200ms after the CTC end of the word) is
  considered non-speech and cut, that is, exactly what was asked not to cut. Spectral
  thresholds (share of energy >3kHz) were tried — the word mask is more reliable:
  a cough is sometimes louder than speech and is not caught by loudness.
  **QUIET frames are not counted as speech by the mask** (2026-07-31): both the wave
  extension and `WORD_PAD` easily drift into a pause (the CTC end of a word is often
  beyond the wave edge), and a masked pause stopped being a hole and stayed in the
  piece. Analysis of the user's manual fine-tuning (6 clips): of 40 segments he cut
  by hand, 22 were exactly such silence — now they go away by themselves, at the
  price of 0.22s of someone else's (and also silence). At the same time we trust the
  silence threshold only if it is 6 dB below the median of the voice: `floor` is the
  20th percentile of the clip, and on dense speech it drifts into the speech itself.
  Audible sighs and "кхм" are not caught by this (they do not differ in loudness or
  in the share of HF from a word tail — measurement: the distributions overlap
  completely, a cough is sometimes louder than speech) — for them there is a
  separate module `core/breath.py`, see below.

**The thresholds were taken from the user's MANUAL cutting** (`NGAutoCut_out/0{1,2,3}_C135{4,5,6}`),
which is the reference here. What the analysis of his files showed: inside his pieces
there is not a single hole >= 0.20s (at most 1-2 segments of 0.15-0.19s) -> `HOLE_MIN
= 0.15`; he sets boundaries ~25ms before a word and ~82-117ms after -> `EDGE_IN_MAX`/
`EDGE_OUT_MAX`. The first version looked for the "quietest point in a ±150ms window"
and stretched his dense cutting by +2s — it was abandoned. Check: `refine_keep`
applied to his own pieces does not change their number and length (±0.5s), boundaries
differ by 33-41ms (2 frames at 60fps). And if his pieces are glued into semantic
blocks (as the AI returns them) and the refinement is run — his own granularity comes
out: 18->24 against his 24, 13->19 against his 21, 12->21 against his 23. That is,
"many pieces" is the norm: pauses and inhales inside a phrase are cut, and one
semantic block turns into 1-2 pieces.

**Sighs and "кхе" (`core/breath.py` + `tools/train_breath.py`, 2026-07-31).** A separate
step AFTER cut refinement — because external models work here, they may be missing from
the environment, and failing because of them in the middle of a job is not allowed
(`available()` -> cutting goes on as before). Three sources, each about what it is good
at:

- **Silero VAD** (~2 MB, ONNX, CPU) — "speech / not speech" every 32ms, 1s per video;
- **CED-tiny** (AudioSet, 5.5M) — a segment **in isolation** (we put it in the middle of
  10s of silence). It separates speech from non-speech perfectly (AUC 1.00) and calls
  long sighs Gasp/Breathing. GOTCHA: CED returns ALREADY probabilities, a `sigmoid` on
  top collapses everything to 0.5. Tiling a segment instead of silence gives a periodic
  hum -> "Music"; a sliding window over the video is useless (AUC 0.53) — neighboring
  speech drowns out the window;
- **acoustics** — loudness, share of HF, duration, place in the piece, pauses around.

No single feature distinguishes a sigh from a word tail (best AUC 0.84), so the decision
is made by **gradient boosting** trained on the user's MANUAL FINE-TUNING:
`project.json` newer than `cuts.json` = the video was edited by hand, and the difference
between the auto-cut and his version is a ready-made label set (34 videos, 3787
candidates, 240 segments he deleted). The model goes to production DISASSEMBLED into
`data/breath_model.json` (thresholds and tree leaves): sklearn is needed only for
training. The estimate is honest — thresholds are selected on N-1 videos and measured on
the held-out one: p>=0.9 -> 22% of finds at 73% precision, p>=0.7 -> 41% at 50%.

Hence TWO working points, not one: `P_CUT` (0.95) — we cut ourselves, `P_MARK` (0.5) — we
write into `<stem>.breaths.json`, the cut editor draws an orange bar, a click on it cuts
the segment (`/api/breaths`, `edBreathAt`/`edCutRange` in `app/70-editor.js`). Plus a hard
safety net `SPEECH_MAX`: speech probability above 0.25 is NEVER cut, whatever the model's
confidence — a false mark is free, a false cut costs a word. The analysis of false
positives confirmed the gate: their speech probability is 0.04, that is, this is not
speech either, the user just did not clean it up.

Retraining: `python tools/train_breath.py` (cache of wav+words in `_breath_cache/`, 34 videos
~30s for transcription). Every cut edit in the editor adds labels — the model is worth
retraining as videos accumulate.
Cost in cutting: ~1.6s per video (Silero 1.0s + CED 0.5s), plus a one-time load of the models.

The function also returns `parents` — which source piece each new one came from.
**The camera layout does not change because of this**: `assign_cameras` is computed BEFORE
the refinement, from semantic pieces, and sub-pieces inherit the parent's camera. Otherwise
the camera would jump right on an inhale in the middle of a phrase (it always gives
neighboring pieces different cameras). Tests: `tests/test_refine_keep.py` on synthetic sound
"word · pause · КХЕ · pause · word".

Words returned by the seam repair are passed into `protect`, otherwise the self-check
iterations loop forever. Tests: `tests/test_gigaam_postprocess.py` — it also holds the
user's reference on a real C1355 piece (`fixtures/c1355_takes.json`): four takes in a row,
manual labeling "drop 32-58 and 68-91, keep 59-67 and 92-100", and three tests check that
the code arrives at it from any starting state — the model dropped nothing / dropped
everything / kept the early take. The rule "the LAST take survives the repeats" is
duplicated in `DECIDE_MARKUP_SYS` too — it was in `core/omni_cut.py`/`aicut`, but got lost
in the move to the word-level path, which is why cutting took the first duplicate.

The self-check loop (GigaAM re-listens to the cut/draft, `max_iter`, `compare_draft` with
restore/drop) was removed 2026-08-10: in practice it made things worse. Seam self-checking
remains a separate step `core/selfcheck.py` (job flag `selfcheck`, engine —
`--selfcheck-model`). Subtitles go the same way: `core/gigaam_subs.py` (subprocess so that an
OOM does not kill Flask) through `core/asr_backends.py` — a shared registry of engines
(Whisper of any size / GigaAM / Omni / CTC models of other languages from
`data/asr_engines.json`).

### Speaker profiles (`core/speakers.py`, `speakers/*.json`)

> Speakers here and below are named by letters: **A**, **B**, **C**, **D**. These are real
> people, and their acoustics, breathing manner and number of re-shot takes are data about
> them, not about the project. The numbers are kept in full, the names are not. The mapping
> lives locally in `docs/speakers-calibration.md` (in `.gitignore`); in the UI the "Speaker"
> selector shows the names you gave to the files in `speakers/`.

The `gigaam_cut.tune` thresholds are not universal constants but a calibration for speaker A.
Measurement of `_envelope` on real clips: for him speech sounds at **39.5 dB** above room
noise (spread 4 dB over 7 clips), for speaker C at **28.8 dB** (spread 14 dB), and on one
clip the margin is only 21 dB, that is, the hard `ONSET_DB = 20` ("voice = louder than
20 dB above the floor") passes ABOVE the median of his speech. The consequence is visible
in the envelope: continuous pieces "above the voice threshold" for speaker A have a median
of 0.39s, for speaker C 0.12s and 90% are shorter than 0.35s. For the code, speech falls
apart into fragments, `refine_keep` cuts the "holes" between them, `drop_micro_keeps`
finishes the rest. Manual edits show exactly that: restores per clip for speaker A 0.74,
for speaker B 1.0, for speaker C 2.25, and 6 of his 18 restores with a length of 0.32-0.50s
are not in `.cuts.json` at all — they were removed not by the LLM but by `MIN_KEEP` /
`MIN_ISLAND`.

A profile = JSON in `speakers/` (gitignored, like `styles/`): the result folder (`outdir`),
the folder for assembled `.jsx` (`jsxdir`), the AE preset, threshold overrides
(`speakers.CUT_DEFAULTS` — the full list), a correction to the decision prompt (`hint`),
appendixes to image generation prompts (`image_prompts` — slots a and b) and reference
frames for future auto-detection. It is selected by the "Speaker" selector on step 1; the
choice sets BOTH folders and the style, travels to `omni_cut --speaker` and is written into
`.project.json`. We do not silently substitute a folder from the profile over a manual one:
empty or a folder of another speaker — we change it right away, our own — we ask (`spkDir`
in `app/95-styles.js`, one rule for both folders).

The profile is created and edited right there, in a window next to the selector (`openSpeaker`
/ `saveSpeaker` in `app/95-styles.js` → `/api/savespeaker`, `/api/delspeaker`). Two rules
that must not be broken in this window, otherwise it silently corrupts data:

- **a COPY of the profile is saved**, not an object assembled from the fields: the file has
  things the window does not (`ref`, `breath_model`), and reassembly would erase them;
- **a threshold equal to the general one is not written to the file** — an empty field shows
  the default as a placeholder. Otherwise "a profile without edits" would get sixteen "own"
  thresholds, and the next edit of the general constants would not reach it.

**The AE style in a profile is a default, not a binding.** Selecting a speaker sets his style;
later, at the assembly step, the style is changed freely and does not go back into the profile:
one person has several styles (one camera / two / another font), and there are always more
styles than speakers. Whose style is currently set is visible on step 1 in the cut summary and
next to the style selector (`styleSpeakerNote`).

**Analysis across all three (`project.json` → `user_overrides` as the reference, word-level
timings taken by GigaAM on 11 clips) — the conclusion is DIFFERENT for each:**

| | restores/clip | of them by thresholds | deletions/clip | what it deletes |
|---|---|---|---|---|
| Speaker A (19) | 0.74 | 1 of 14 | 2.68 | inhales inside pieces (peak +19.6 dB) + meaning |
| Speaker B (17) | 1.00 | **0 of 17** | 3.71 | talks with the cameraman, re-shoots |
| Speaker C (8) | 2.25 | **6 of 18** | 2.50 | speech (level 25.5 dB at speech 28.0) |
| Speaker D (18) | 1.78 | **17 of 32** | 0.89 | pauses 0.31–0.90s without text (holes under `hole_min`) |

- **Speaker C — the threshold.** Four of the six "threshold" restores turned out to be words
  ("трехста", "сохраняйте", "читайте") with a speech length of 0.52-0.60s, that is, islands
  shorter than `MIN_KEEP=0.6`. Plus a low speech margin. It is fixed with numbers.
- **Speaker B — meaning.** His thresholds are NEVER wrong, although acoustically he is closer
  to speaker C than to speaker A (margin 25.6 dB, 85% of pieces "above the voice threshold"
  are shorter than 0.35s). A low margin by itself does not break anything — this is important:
  `db_auto` should not be enabled for everyone "for company". He is cured by `hint`.
- **Speaker A — inhales.** His pauses inside pieces are not silence but inhales: the speech
  mask extends a word BY SOUND, the inhale sticks to the word, and for the code there is simply
  no hole — lowering `hole_min` is pointless. He is cured by the breath detector, but the
  general threshold `P_CUT = 0.95` is too cautious on him.
- **Speaker D — the `hole_min` threshold.** 17 of his 32 restores are pauses of 0.31–0.90s with
  EMPTY text (median 0.54s), that is, holes without speech, not words: they were cut by code,
  not by the LLM. There were no overrides in the profile (`"cut": {}`), the defaults taken from
  speaker A were working. The cost of the threshold over 200 cut holes in his 18 videos:

  | `hole_min` | holes returned | extra air per video |
  |---|---|---|
  | 0.15 (default) | — | — |
  | 0.45 | 17 | 0.23 s |
  | 0.75 | 23 | 0.43 s |
  | **0.95** | **41** | **1.29 s** |
  | 1.20 | 61 | 2.48 s |

  `hole_min = 0.95` was set: with it all seventeen returned pauses survive (maximum 0.90s),
  the price is 1.29s of extra air per video. The second half of the complaint (14 of 32) is cut
  by the LLM.

**The "cut in the middle of a word" metric** (the share of piece boundaries after `refine_keep`
that landed inside a word) — it was used to select speaker C's `hole_min`: 25.1% for him against
8.1% for speaker A and 18.1% for speaker B; `hole_min` 0.15→0.22 drops it to 13.5%, together with
`db_auto` to 11.3%, and at the same time not a single extra hole >0.31s remains. On speaker A no
threshold sweep changes anything (8.1% at any) — one more confirmation that the defaults are his
calibration.

**Breath detector: the THRESHOLD must be personal, not the model.** I trained one per speaker
(`train_breath.py --speaker`) and compared leave-one-clip-out on the same 44 clips: the common
model catches 25% of sighs at 76% precision, the three personal ones together — 21.8% at 74%.
There is too little data for one (906-2725 examples against 4914), and the features of a sigh
turned out to be not as personal as the sound of a voice. But the CALIBRATION of probabilities
diverges strongly: with the common `P_CUT = 0.95` the detector takes 18 of 151 sighs for speaker
A, 23 of 91 for speaker B, 9 of 75 for speaker C. The threshold for 75% precision on held-out
videos: speaker A 0.88 (31 of 151), speaker B 0.95 (as is), speaker C 0.84 (19 of 75) — twice as
many caught at the same error cost. That is why `breath_p_cut` lives in the profile, while
`breath_model` is left for the future (when a speaker accumulates a comparable volume).

The thresholds are put into the MODULE constants of `gigaam_cut.tune` once at the beginning of
`run()` (`apply_speaker`) — they are read by a dozen and a half functions across the file. Because
of this, thresholds that used to be argument defaults (`thr=SILENCE_SEC` and the like) were changed
to `None`: a default is bound at import and does not see the profile.

`db_auto` computes loudness thresholds from the REAL speech margin in the clip, not in absolute dB:
`snap = 0.30 × margin`, `onset = 0.50 × margin`. The shares are not invented — they are the same
12/20 dB recalculated for speaker A's margin of 39.5 dB, so on his material the formula returns
12.0/19.9 and changes nothing, while for speaker C it lowers the thresholds to 6-10 / 10-17 dB per
clip. It is enabled by the profile, off by default.

The defaults in `speakers.CUT_DEFAULTS` are duplicated from the `gigaam_cut.tune` constants — a
divergence would mean that "a speaker without edits" silently changes cutting. It is verified by
`tests/test_speakers.py` (it also checks that every threshold from the list actually reaches the
constant).

### Engine 3 — "After Effects" (final Premiere XML → `.jsx`)
Assembly goes in TWO steps so that the step-3 preview sees exactly the numbers that will go into
AE (the rule "every value has one source"):

1. **`scene_plan()`** (`core/xml2ae/build.py`) — the scene plan WITHOUT roto and writing; it is
   now itself an ASSEMBLER: the computation is laid out across the blocks `plan_words` /
   `plan_assets` / `plan_subs` / `plan_intro` / `plan_intro_tpl` / `plan_inserts` / `plan_decor` /
   `plan_audio` / `plan_camera`, and in `build.py` only the XML parsing, the door calls and the
   distribution of the result by keys remain. The plan holds: camera push-in and drift, insert keys,
   intro groups (windows `ts`/`te`, offsets `dx`/`dy`, scale `ds`), subtitles, the visual caption
   of the video (`caption` from `<stem>.caption.json` by the preset style — plate + text), roto
   markup, `audio` (loudness + censor windows). It is returned as a plan via `/api/scene`; the
   preview draws only it, computing nothing on top (`scene_plan` lives BEFORE roto — the preview
   does not touch the GPU).
2. **Precompute** (`core/xml2ae/precompute.py`) — head track, roto masks and highlight strength
   (`<stem>.emph.json`) with the SAME functions for assembly and for the preview door: there is no
   second copy of the computation, the caches are shared. `precompute()` calls `head_track` →
   `emphasis_precompute` → `roto_masks`; the strength of highlights is computed BEFORE the plan (the
   plan reads the sidecar and decides from it which highlights get a push-in), while `cached_plan()`
   answers "what has already been computed" from the caches without a GPU — a fast preview door. RVM
   is unloaded right there (`_release_roto`): a model forgotten in video memory hangs the machine on
   the next computation. The preview button "Compute roto and tracking" (`api/previewcalc.py`) goes
   through the same door.
3. **`to_ae_full()`** = plan + roto (RVM alpha of the character, GPU) + substitution into the JS
   template `AE_FULL` → writing the `.jsx`. Separately, `build_combined()` glues several files into
   one `.jsx` (a set → "one for all").

**Assembling a set with several AE copies.** A set of videos is assembled not by one `AfterFX` copy
one after another but by N copies (`AfterFX -m -noui`), each handling its part of the clips into a
fresh project; then one copy merges the parts into a single `reelsi_batch.aep` (importing the parts,
merging repeated footage, the render queue as before). The setting `ae_build_workers` from
`ai_config.json` (`auto` = min(3, number of clips, free RAM / 10 GB); `1` — the previous path). The
`.jsx` is still ONE for the whole set: a part builds only its own timelines
(`$.global.REELSI_ONLY`), so running the `.jsx` by hand in AE does not change. Measured on 12 clips
(AE 26.2): one `AfterFX` — 1534 s (time per clip grew 10 → 284 s: AE accumulates undo history and
the project grows), 3 copies plus merging — 237 s. Hygiene: old logs of the parts and of the merge
are deleted before the start, an `.aep` not re-saved by this run fails the videos (we do not go to
`aerender`), "Stop" kills all copies as a process tree by their PIDs.

**Headless render** (trio `api/render.py` [routes] → `core/render_job.py` [orchestration] →
`core/aerender.py` [stateless engine]). The "Render" button on step 3 assembles the project
(`AfterFX.exe -noui -r …jsx` — with `app.project.save` and without `alert`, only in this mode) and
hands the render queue to `aerender.exe`; the progress is parsed from stdout into the job log.
Windows only: AE is searched under `%ProgramFiles%\Adobe` (`find_ae` in `core/aerender.py` — a single
source for `api/render.py` and `doctor.py`), output presets are tied to the installed version.
Cancelling the job kills the `aerender` process so that it does not stay hanging in the background.
Both branches start `aerender` with `-mem_usage 40 60` (the constant `AERENDER_MEM_USAGE`): without a
limit AE takes almost all memory, Windows goes into swapping (+513 MB on one video) and the render runs
SLOWER — 361 s against 290/296 s; with the limit 34–40 GB are free and swap does not grow (it spares
the SSD).

**Two render paths.** A set of ONE video — `_run_render_single`: headless `.jsx` with the
`_render_tail` tail (queue + save + quit inside the script), `AfterFX -noui -r`, `aerender -project`.
A set of SEVERAL videos — ALWAYS `_run_render_combined` (one common `Reelsi_all.jsx`, the master runs
exactly this file; the `multimode` radio does not affect the render and serves only manual assembly
"Assemble set"). `build_combined` assembles all videos with `comps_global=True` and bin prefixes; the
preflight `verify_jsx` runs once over the whole common file (a failing video stops the whole set). The
master script runs `Reelsi_all.jsx`, where each timeline writes `таймлайн ok:` into the master log,
collects all compositions into the common render queue, saves the `.aep` into the set folder and quits;
then one `aerender -project` follows. The tail rules are preserved: the log as a file first of all,
`om.file` after `applyTemplate`, `app.project.save` before the queue and once more after. The progress
goes by queue items: before `aerender` all are `render`, on "Finished composition" the next one is
`item_done`.

## Web UI (structure after the 2026-07-17 refactoring)

**The backend is the `api/` package**: ALL `/api/*` routes, JOB/LOCK/emit/job_start, cutting and
assembly jobs — in one Flask blueprint. The front end does `app.register_blueprint(api.bp)`.
Backend edits happen only here. Until 2026-08-06 this was one 2617-line file; now there are modules
by topic, and nothing changed from the outside (`import api`, `from api import bp`):

| module | what is there |
|---|---|
| `api/_core.py` | Blueprint, JOB/LOCK/emit, job_start/finish, interprocess lock, shared paths |
| `api/jobs.py` | cutting jobs: `/api/run`, `/api/omnicut_run`, `/api/cancel`, `/api/status` |
| `api/files.py` | cameras, filtering out already cut takes (`/api/newtakes`), native pick dialogs, `/api/media`, `/api/ui_state` |
| `api/presets.py` | style presets, speaker profiles, terms |
| `api/ai.py` | provider profiles and keys, model list, single AI calls |
| `api/editor.py` | words, subtitles, cut editor |
| `api/build.py` | `.jsx` assembly, `/api/scene` (scene plan for the preview), camera layout |
| `api/inserts.py` | insert library |
| `api/videogen.py` | the "Video" tab (its own `VJOB` state, does not touch JOB) |
| `api/previewproxy.py` | 720p 4:2:0 8-bit preview proxy for 4:2:2 10-bit sources (Sony/Canon) that the browser does not decode and 4K seeks hang: `/api/preview_proxy` + `/api/preview_proxy_status`, its own `PXJOB` state |
| `api/gdrive.py` | downloading material from Google Drive via `rclone`: `/api/gdrive_download` + `/api/gdrive_status`, its own job (does not take JOB), the progress is parsed from the rclone output; link parsing, the command and progress parsing are pure functions of `core/rclone.py` |
| `api/render.py` | a thin HTTP module of ~162 lines: the routes `/api/render_run` and `/api/render_status`, the job instance (`RJOB`, `RLOCK`) and "Stop"; all orchestration was moved out into `core/render_job.py` |
| `api/previewcalc.py` | the preview door "compute roto and tracking": `/api/preview_calc` (button, background computation via `core/xml2ae/precompute.py`), `/api/preview_calc_status` (progress by pieces and "what is already computed"), `/api/preview_calc_cancel`. Its own `PCJOB` state, a shared task lock (`_cross_lock_acquire`): the heavy GPU stage does not run at the same time as cutting, assembly and render. The body is the same as `/api/scene` and is normalized by the same door, otherwise the plan and the masks would diverge |
| `api/voicefx.py` | speaker voice: denoiser, VST chain, live host (`/api/voicefx_live`, commands `play`/`seek`/`pause`/`track`/`chain`/`gain`/`dump_states`), plugin window (`/api/voicefx_host`, `_host_edit`, `_host_stop`), baking the final voice (`<stem>.voice.<key8>.wav` + the sidecar `.voice.json`; `/api/voicefx_bake`, `..._status`, `..._cancel`). The sound is computed by `core/voicefx.py`, the live host by `core/voicefx_editor.py` (its own process per clip, only while the plugin window is open) |

The layering rule: `api/` — routes and job state, the engine — in `core/`; `core/` does not import
`api` and `flask`, `api/` does not import the CLI (`reelsi.py`). Guarded by `tests/test_layers.py` and
`tests/test_api_no_cli.py`.

There are two places where the split is not mechanical, and this is important to remember when editing:
`CURPROC` (the handle of the `omni_cut` subprocess) lives in `jobs.py`, not in `_core`, because it is
REASSIGNED — `from ._core import CURPROC` would bind the name once and "Stop" would kill `None`. For the
same reason path substitution in tests goes through the owning module (`api.videogen.VIDEO_OUT`), not
through the `api` facade.

JOB carries `kind` (cut|draft|build), `label` and a structural `progress {i,n}` — the client draws the
bar without sniffing the log.
`/api/ui_state` — the server-side mirror of the UI state (`ui_state.json`, in .gitignore):
localStorage is the primary one, the server survives a browser change/cleanup/quota.

**The UI is `webui.py`** (port 5001) — a Flask glue plus serving
`templates/index.html` (re-read by mtime — a front-end edit is visible on F5 without a restart) plus the
static `static/app.css` / `static/app/*.js` (cache-busting `?v=mtime`).
The translation dictionary (`static/i18n/en.json`) is always embedded in full (~300 KB per page load,
including with the Russian interface), because a manual language choice in localStorage is stronger than
the server default of `ui_lang()`, and loading it with fetch caused a race with the camera strings.
Since 2026-08-06 the interface code is split into separate files `static/app/*.js` (`00-core.js`,
`10-settings.js`, `20-widgets.js`, `30-video.js`, `40-queue.js`, `50-chrome.js`, `55-progress.js`,
`60-preview.js`, `70-editor.js`, `80-inserts.js`, `85-inserts-view.js`, `86-lut.js`, `87-camframe.js`,
`87-roto-preview.js`, `88-cams.js`, `90-ae.js`, `94-stylepanel.js`, `95-styles.js`, `99-boot.js`; it used
to be one file of 3827 lines).
They are loaded by ordinary <script> tags into a SHARED scope, as when it was a single file — not as
modules. That is why the order matters: function declarations are hoisted within their own file, and
code that runs on load (`99-boot.js`) must be last.
The order is set by a numeric prefix in the name, the file list is taken from the folder
(`app_meta.app_js_files`), and the server builds the tags: a hand-written list diverges sooner or later,
and a forgotten tag gives no error in the console — part of the interface simply stops responding.

A 3-step master (Cut → Markup → After Effects), AI cutting, the cut editor, camera layout, a progress
overlay with a "Stop" button.

**Clips appear in the list ONE BY ONE while the queue is being cut (2026-08-05).** The server was already
adding to `JOB["results"]` after each file, but the client fetched them only in `onDone`: with an
hour-long queue the finished XMLs lay on disk and there was nothing to edit. Now `pollJob` accepts a fifth
argument `onTick(d)` — it is called on EVERY poll — and cutting attaches `cutAdopt(d)` to it: new names
from `results` become clips (`clipByXml` suppresses repeats), the step-1 list and the statuses update
immediately. The progress overlay covers the page, so its footer gets a "Edit finished: N" button
(`#progReady` → `progToReady`) — it collapses the overlay into a chip and takes you to step 1.
Because of this, `refreshStatuses` stopped silently skipping a request that arrived during a pass
(`REFRESHWANT`): a clip that flew in at that moment stayed without subtitle/highlight tags until the end
of the queue. The cut editor, camera layout and markup on finished clips work in parallel with the job —
their routes (`editor_load`/`editor_save`/`waveform`/`cams_*`) do not take JOB and run on the CPU, they do
not take VRAM from cutting. The contract is guarded by `tests/test_ui_static.py`.

**Step 1 — a queue on ONE camera (2026-08-11).** Auto-pairs and both audio-based matchers need two
cameras, so with one camera there were no bulk buttons at all: twenty files went into the queue as twenty
pairs of clicks on the selector. The "All files to queue" button (`autoQueue`, visible only with one
camera) takes from the folder everything not yet in the queue and respects the same "only new" checkbox.
The visibility of the buttons by the number of cameras is set by `camModeUI` from `buildCamRows`, not by
`setMode`: `setMode` is called only on a radio CHANGE, and after F5 with a saved single camera the page
still had the buttons for two.

**Step 1 — "only new" (2026-08-11).** A camera folder accumulates shoots, and what needs to be cut is
what arrived from the last one. The checkbox next to the bulk buttons ("All files to queue", "Auto-pairs
by name", "Match all by sound", on by default) runs the file list of camera 1 through `/api/newtakes` and
keeps those for which there is no XML in the result folder yet. For sound matching this is also its whole
cost: correlation is computed for every file, the filtering happens BEFORE it. "Already cut" is determined
in three ways, from exact to rough: the run's `<stem>.project.json` (it holds camera paths, the file is
tiny), the `<pathurl>` inside the XML itself (the sidecar was lost; the XML weighs megabytes, hence the
second queue) and the XML name with the queue prefix stripped ("03_C1432.xml" → "C1432").
Names are compared in lowercase, `.xml.bak` does not count. A request error or an empty result folder =
"all are new": silently losing a duplicate is worse than offering an extra one, and how many were filtered
is written to the log. The contract is `tests/test_newtakes.py`.

**Step 2 — spot cleanup and color (2026-07-22).** On hover, the tags "subtitles / highlights / inserts
X of Y" show a ✕ (`clearPart`): subtitles are wiped by `/api/clear_subs` (rebuilding the XML without
`sub_words` — exactly what it was before `gen_subs`; highlights go away with them, they live as a COLOR on
the title words and there is nowhere to store them separately), highlights — by an empty `/api/set_yellow`,
inserts live only in the UI state. The clip block on step 2 is NO LONGER colored green as a whole
(`.clip.ready` is not set there) — only the tags themselves are green; on step 3 the frame remains, there
it means "ready for assembly". In insert cards the green frame (`.inscard.chosen`, and `.itlblk.chosen` on
the strip) is now only on a file chosen CONSCIOUSLY: auto-match and generation do not get it (they have
the "auto"/"gen" tags), otherwise "green = verified" lost its meaning — the list lit up by itself right
after markup.
The "currently on screen" marker (`.inscard.playing`) became yellow and thick: a thin amber outline on a
green card was unreadable.

**Pipeline markup (`markupAllRun`, `markupPlan`).** The batch markup of step 2 ("Mark all") is
orchestrated through a pipeline: subtitle generation runs strictly sequentially on the GPU (1 video at a
time). As soon as the subtitles of a particular clip are ready, that clip is immediately passed into the
queues of the next steps (highlight words and inserts). Cloud steps (`!step_local[step]`) start processing
immediately, without waiting for the subtitles of the other clips of the set, and can run in parallel with
each other (highlights and inserts for one clip start simultaneously). Local steps (`step_local[step]`)
wait for all subtitles to finish before starting, to save VRAM; if both highlights and inserts are local
and use different models, inserts wait for all highlights to finish (to avoid unloading and reloading heavy
weights). An error in one step does not interrupt other phases and clips. A clip is considered ready
(`done`) when all selected phases are finished.

**UI style** — SEE `docs/DESIGN.md` in the repo (the main style document): obsidian #101010,
hairline borders #212121, weight 400 + uppercase in headings, a white pill = the main action, radii 4/8px,
no shadows; icons are 1.5px SVG outlines (chalk/gold #6f6759), emoji are forbidden in the chrome.
Pulse Green #98ff38 — ONLY for "done/worked" statuses (user request). All help lives in "!" tooltips
(hover and focus), there are no visible inline hints. Yellow #f5c518 and the photo/video colors on the
insert timeline are functional data, do not repaint them.

Added 2026-07-16: a mini-player in the camera layout (CPV — the picture by the edit, sound from the
selected camera to check sync); the "Words" panel in the preview (highlights straight into the XML blobs
via `/api/set_yellow`, the intro into the step-3 job, editing a word's text via `/api/edit_word`); a green
"✎ edited" mark on clips; the camera layout button hidden for 1-cam; space in modals always play/pause;
the insert library (see `core/insertlib.py`).

Changed 2026-10-02: **on step 1 there is ONE player — the cut editor.** The "Edit" block with a second
player on the same video was removed: two players fought over `currentTime`, and a click far along the
timeline during playback rolled back to the start of the next edit piece (the video was driven by the
second player by its own piece number). The playhead and the picture are driven by `ED`
(`static/app/70-editor.js`), the volume and the "camera — cuts — length" line moved into the editor's
control row. The processed speaker voice (the `vt*` denoiser track and live VST plugins) sounds in the SAME
player, both in "listen to the edit" mode and when the source plays: the sound time is the ORIGINAL time of
camera 1 under the playhead (`ED.cs`), and at cut-out places the sound jumps together with the picture.
What exactly plays — the baked final voice or the live chain — is decided by one rule: the live host only
while the plugin window is open (see below).
`ED.cams` (set by `openPreview`) gives the voice track and the live host the camera-1 file, and
`ED.voicePanel` — which "Voice" panel to take the live knobs from on screen. The "Voice" panel on the
first opening of the preview is read AFTER the editor is opened, otherwise the clip's speaker was not found.

**The live voice host (`core/voicefx_editor.py`, `--live`)** — a separate process per clip: it plays the
track (voice after the denoiser) through the enabled VST plugins in sync with the picture (the commands
`play`/`seek`/`pause`/`track`/`chain`/`gain`/`dump_states` from stdin) and opens the plugin windows on the
MAIN thread (JUCE cannot do otherwise) without interrupting the sound. The output stream is opened at the
DEVICE rate (`sample_rate=None` when 48 kHz is refused), and a streaming resampler sits at the output of
the chain: the plugins compute at 48 kHz as before, while `write` receives the stream rate. An audio failure
(device, stream) goes as an `audio_error` event into the status of `/api/voicefx_live` — on it the page
returns the sound to ITSELF instead of muting its own voice in favor of a silent host. The host volume =
speaker voice volume (dB) + listening volume (20·log10).

**Format: live plugins only while the window is open (changed 2026-10-06).** The host is up while the
window of at least one plugin is open: then the sound goes through the chain live ("twist and listen"), and
the player connects the DENOISER TRACK (without plugins — otherwise the chain would be heard twice).
Windows closed — the settings of ALL plugins are saved, the voice is re-baked, and ALL steps (step 1 `ED`,
step 2, step 3 `IPV`) play the FINAL baked voice (`final: true`, the version file with the chain) from the
cache. Why: the host is a separate process, and it cannot be fitted to the picture more precisely than 0.4 s
— with VST enabled the preview lagged ("it did not finish the ends, sometimes played more and in the wrong
place"), with them disabled it went smoothly.

Closing the last window and killing the host (closed the preview, switched the clip, left the step) begin
with the `dump_states` command: the host returns the state of EVERY loaded plugin (including a disabled one
— it is kept loaded and its knobs are also turned) with a `states` event carrying the list
`[{path, name, state_b64}]`. The server writes them into the speaker profile BY PLUGIN PATH in one save
(`api/voicefx.py:_save_live_dump` → `_save_live_states`), and only then is the host removed by PID
(`core/voicefx.py:live_stop` with a hook). Without this, killing with a window open would lose the tweaks:
only the host process knows the state, and removal by PID no longer answers. While the new voice is being
baked, the PREVIOUS baked track plays (not the camera sound), the voice line says "voice is being
recomputed…"; when done — the `<audio>` source is swapped without a pause. The host input is the denoiser
track, if it is enabled and already computed; the raw sound is left only to those who have nothing else to
play (no cache — while RoFormer is computing, the person needs to hear the voice, not silence).

Sync of its own track (`vtTick`, `static/app/60-preview.js`): a discrepancy of 0.03–0.25 s is damped by
SPEED (`playbackRate` ±6 %, like the cameras — `camTrack`), more than 0.25 s — by seeking, while on
pause/scrub and on a jump across a cut (`vtSeekAt`, from `edJump`) the position is set exactly and
immediately. Seeking every 0.15 s was what sounded like "played in the wrong place".

Added 2026-07-23: **a shared volume control** for all three video previews (edit `PV`, inserts `IPV`,
layout `CPV`). A slider `<input data-vol>` in each panel, one value `MEDIA_VOL` (localStorage
`autocut2_vol`, 0..1) for all players — `setMediaVol` writes the key, applies it to ALL `<video>` (only
the non-muted one is audible) and synchronizes all sliders; new `<video>` elements take `MEDIA_VOL` when
created.

**Preview sound and the Web Audio graph (2026-10-07).** `createMediaElementSource` is an IRREVERSIBLE
door: after it the element gives sound only into the graph, and a suspended `AudioContext` (the browser
keeps it `suspended` until there was a live gesture) mutes that path entirely and silently — this was the
defect "video plays, no sound". Therefore only what NEEDS the graph goes into it, and there are exactly
two doors, each its own:

- **`audioWake()`** — one door for waking the context, called from the click handler by all players
  (`edPlay`, `ipvPlay`, `cpvPlay`). Before, `AUDIO.resume()` was in EXACTLY one place — in MUSIC
  synchronization: the step-3 player sounded because `ipvPlay` called `musicSync` at the end, while no one
  woke the graph for the step-1 player.
- **`voiceGraphNeeded()`** — the rule for whether the graph is needed because of volume: the voice volume
  of the style (`voice_db`) can only be done by `VG.gain`, the element's `volume` cannot exceed 1.
- **`voiceWiring(v)`** — a request for connection (the element is created, the decision later),
  **`voiceEnsure()`** — "the graph is needed": it connects the `VOICEPEND` queue and wakes the context.
  It is called only by the doors where processing actually sounds (`vtGate` with the gate open,
  `vtEl`/`vtSpareOf` when creating a voice track) and by `applyDbGains`. The only place where
  `createMediaElementSource` is called is `voiceGraphWire`.

A speaker WITHOUT processing does not touch the graph at all: the camera sound plays directly and does not
depend on the state of `AudioContext`. Before, the camera sound was unconditionally routed into the graph,
and even on element creation.

**Firefox — a line by FACT, not by guess.** The material is written as `pcm_s16be` in MP4; Chromium reads
this track (measured 2026-10-07: Chrome 152 on Windows and Linux), Firefox does not. The property
`<video>.mozHasAudio` exists ONLY in Firefox, so the code asks the element itself (`fxAudioFact`): the
answer is remembered on the element TOGETHER with its `src` (moving to a video proxy changes the source —
the previous answer was not about it), and the line "Firefox does not read the sound of these cameras —
sound will appear with the proxy" is added by a DEFERRED entry (`FIREFOX_FACT_MIN = 1.5` s of playback;
before the first decoded frame `mozHasAudio` means nothing) and is removed when the cause is gone. In
Chromium the line never exists. The recommended browser is Chromium-based (Chrome, Edge, Brave).

**There is NO audio proxy — it was removed deliberately.** The previous version assembled an `.m4a` first
and played it as a separate `<audio>` where the source was "unreadable". On Windows Chrome reads the
camera sound `pcm_s16be` fine, and the proxy played ON TOP of the camera sound: the voice louder (in phase)
and doubled (with an offset), while muting the camera was applied only on a camera change. Therefore it
was removed at both ends: no `<audio>` proxy, no `media.probe_audio_codec`, no
`_audio_proxy_plan`/`build_audio_proxy`, and no `audio` field in `/api/preview_proxy` — the route is a
plain video proxy again, and its track is aac. The guard is `tests/test_preview_audio.py`.

**`pedalboard` is declared.** It was not listed anywhere — neither in `requirements*.txt`, nor in
`pyproject.toml`, nor in the installers, nor in `doctor.py` — so the only way to learn about it was to
enable plugins. Now it is in `requirements-optional.txt` and in the list of optional packages of
`doctor.py` ("live monitoring through VST3 plugins and the list of output devices"). The output device
list is not tied to it: without the package `/api/voicefx_devices` answers with devices from the audio
system (Windows registry, `core.voicefx.system_output_devices`) and a `reason` field with the cause,
not with an error about plugins; the `reason` is shown in the panel (`static/app/95-styles.js`,
`fxDeviceFill`), not just written to the log.

Changed 2026-07-21: **step 3 (After Effects) no longer marks up the intro and highlights** — both are done
in the preview (`openAEPreview` → the `#aewpanel` panel: a single word list, see below; the `#intromode`
select and "AI intro" are there too). The DOM nodes `#introgroups`/`#words`/`#introcount` are gone,
`renderIntro()` only re-sorts the groups (`introReorder`) and updates the overlay, the word counter is
`wordsInfo()`.

**Batch AI intro (2026-07-30).** `aiIntroAll`/`aiIntroAllRun` mark up the intro in all files checked on
step 3 at once (`selClips`: none checked = all) — before, you had to open the preview of each clip and
press "AI intro" separately. The button sits in two places: in the "Set files" header (`#introall`, where
the checkboxes also live) and in the intro bar of the preview next to the single one; the counter in the
label is shared, `introAllCount()` by `[data-introcnt]`. It follows the same template as `markupAll`:
`progShow`/`progUpdate`/`progDone` + `uiBusySet`, "Stop" through `UICANCEL` (breaks after the current
file) and `aiPost` (the tail of the server log into the overlay label). A clip without subtitles is
skipped with its own line in the log, the skip counter is in the final message. An already marked-up intro
is REPLACED by the AI, so before the start there is a `confirm` with the list of such files. A clip open in
the preview is updated in the panel too (`INTRO`+`aewRender`), otherwise the old markup would remain on
screen. The single `aiIntroRun` now starts with `uiBusyGuard()` — the batch run goes through `aiPost`,
which does not take `AIREQ`, and without the guard two AI calls could go in parallel.

Changed 2026-07-23: **the "Settings" card (`#aecfg`) on step 3 is hidden entirely** (user request — "no
longer needed"). `selectAE` now keeps it `display:none`; only "Set files" and "Assembly" remain on the
page. The preview (style, intro, highlights, inserts) opens by clicking a clip (`openAEFor` →
`openAEPreview`). The DOM of the card is NOT removed — `#stylebox` still moves into the "Style" tab of the
modal and back (`styleToModal`/`styleHome`), and the style fields are read by `captureAE` even when hidden.
**The `#aewpanel` panel is ONE word list for everything (2026-07-30).** The tabs "Highlights · Intro ·
Edit words" are gone: the user works with the same text, while switching was needed for every word. Two
modes remain (`AEWMODE`): `words` and `style` (`#stylebox` still moves into the latter). In `words` mode
the intro bar (`#aewintrobar`: `#intromode`, "AI intro") and the group list (`#aewintro`) are always
visible above the list, and words that went into the intro are removed from the common list
(`introConsumed`) — they are already shown in the groups. Three gestures on a chip:

- **click — the subtitle highlight** (`HL`/`BRK`, as on the "Highlights" tab);
- **double click — the word goes into the intro as an accent** (`aewToggleAccent`: the line
  `{count:1,color:'white',break:true,from:wi}`, a repeat in the group list removes it). White, not yellow
  (2026-07-30): yellow was set ALWAYS, and all manual finishing came out yellow — 75% against 42% for the
  AI on the C1387–C1395 set. Yellow is now conscious, a checkbox in the group line;
- **Ctrl/Cmd+click — editing the word text** (`aewEditChip` → `/api/edit_word`).

The same three from the keyboard: `Enter` / `Shift+Enter` / `Ctrl+Enter` on a focused chip.
Yellow is set IMMEDIATELY on the first click, not deferred by 220ms as the accent used to be (`AEWCLICK`
removed): this is the most frequent action and a delay on it is noticeable. A pair of clicks of the double
click toggles yellow back and forth, and `aewUndoClicks` before the word goes into the intro returns
`HL`/`BRK` exactly to the state before the first click (the snapshot is put by `aewMark`, lives 450ms).

**Because of this, yellow does NOT rebuild the list** (`aewSyncLight`/`aewPaint`, `pvwPaint`) — it only
toggles classes on already existing nodes. Chrome issues `dblclick` only when both clicks landed on the SAME
node; the first version called the full `aewRender()` on click, the chip was replaced, and "double click =
into the intro" did not work AT ALL. Synthetic events do not catch this (`dispatchEvent('dblclick')` always
passes) — only real clicks should be used for checking. For the same reason the stack break `|` is drawn
BETWEEN EVERY pair of neighbors and hidden by the class `.brk.hid`, instead of being created in place:
the appearance of a node next to a chip is also a structural edit.

**A word that went into the intro is edited BY CLICKING IT IN THE GROUP LINE** (2026-07-30). It is not in
the common list (`introConsumed`), so Ctrl+click on a chip does not reach it — there was nowhere to change
the text at all. The words of a line are drawn as separate `.iword` (in `introRowHtml`, that is, in both
panels at once), a click opens the same inline input and the same `/api/edit_word` (`introWordEdit` +
`aewEditIntroWord`/`pvwEditIntroWord`: a shared editor, different save/rollback). A modifier is NOT needed
here — in an intro line a word has no other actions, yellow and "into the intro" no longer apply to it. Esc
stops propagation, otherwise the modal would close.

"Yellow" in the intro is the `color` of the accent line itself, not the HL set of subtitles (a word in the
intro is cleaned from highlights anyway by `hlDropIntro`). The previous two-step "select a word
(`.chip.sel`/`AEWSEL`) → button 'accent from the selected word' (`aewAccent`)" was removed back on
2026-07-23. The editing of the `pvw` panel (cut preview, step 1) was NOT touched — there a click on a word
in the intro still seeks (`pvSeekTo`).

### Intro lines and accents: selectors, effects, counter and 5 doors

The intro line (`introRowHtml` in `static/app/60-preview.js`, `job.introRows`, `PVW.intro`, `INTRO`) is
configured by independent selectors:
- **Color (`color`, `fill`):** `white`, `yellow` (in the UI — "highlight"), `accent` (the accent color of
  the style), `custom` (arbitrary RGB with an `<input type="color">` palette). In intro lines the words are
  displayed in the highlight color of the style `var(--introhl, var(--subhl, var(--yel)))`;
- **Line appearance animation (`anim`):** `""` (smooth fade), `up` (flies in from below), `left` (from the
  left), `right` (from the right), `glitch` (text glitch through character-by-character flicker of letter
  opacity without substituting symbols, repeating the AE Text Animator with `Opacity=0`,
  `Randomize Order=1`, seed 10), `reveal` (reveal: a character-by-character cascade of scale from 0 to
  100% left to right with blur). The `count` option was removed from the `anim` selector;
- **Effects (`fx`):** `""` (none) or `glow` (the `Glo2` glow effect with screen blending);
- **Font roles:** `accent` (the accent font and case of the style) and `back` (background with 69% scale
  and the `back_font` font);
- **The number counter (`is_count`, `cnt_words`):** it is set on EVERY number word of the line
  independently (`isNumberWord`), each such word has its own `123` chip (`.icnt`). The positions of the
  counter words are stored by `cnt_words` — a list of positions inside the line (`0..count-1`), `is_count`
  always equals `cnt_words.length>0`. Jobs without `cnt_words` are read as legacy (the counter on the first
  number of the line), and the first click on another number materializes the list (`[p0]` → `[p0, p]`). A
  click touches ONLY its own line: the previous rule "one counter per intro" is cancelled. The manual
  precision input in the UI is hidden — the number of digits after the decimal point (`dec`) is computed
  automatically from the text of the number: for integers (for example, 100, 1500) `dec=0` (`Math.round`),
  for numbers with a fractional part through a dot or a comma (for example, 2.5 or 2,5) `dec=1`
  (`.toFixed(1)`), for 3.14 — `dec=2`. In After Effects (`core/xml2ae/build.py`) the `Slider Control`
  (0 → target number over `HL_DUR`) and the expression on `Source Text` are independent of the string
  `anim`: the string animations (`glitch`, `reveal`, `up` and so on) and effects (`glow`) are normally
  applied on top of a counter number word. In word-level mode the plan returns `cnts`
  (`[[position, target, expression, dec], ...]`), and EVERY number-word layer gets its own `Slider Control`
  and its own expression — counters of one line do not conflict; the scalars `cnt`/`expr`/`dec`/`cnt_idx`
  equal the first element of `cnts` and serve the line mode (one text layer — one counter, only the first
  number of the line).
- **Five synchronization doors:** the intro line data must be passed synchronously and without loss through
  all entry and exit points: `selectAE` (step 3, job → panel), `captureAE` (step 3, panel → job),
  `aiIntroRun` (AI intro of the current file), `aiIntroAllRun` (batch AI intro) and `resolveIntroFor`
  (resolution into assembly and into the preview plan). Both AI doors read the model answer with one
  function `introRowsFromAI`. The fields `count, color, fill, anim, fx, dec, is_count, cnt_words, break,
  from, gx, gy, gs, accent, back` are saved in all doors.
- **Frame-by-frame preview in the web player (`static/app/85-inserts-view.js:ipvIntro`):** an exact
  reproduction of the intro animations and styles without opening After Effects. The computation is
  frame-by-frame from the player timing `tm` (in `static/app.css` the `transition: opacity` delay is
  disabled for `.ipvintro span` and `display: inline-block` is enabled). Supported are the `ipvEase` curve
  (cubic-bezier 35/90), the appearance animations `reveal` (character-by-character cascading scale 0→100%
  and comp blur), `up` (flies in from below), `left`/`right`, `glitch` (character-by-character flicker of
  letter opacity) and `fade`. The exact base scale of the intro precomp is calibrated with the coefficient
  `0.968` (`iSc = 96.8 * gDs / 100` in AE). The `is_count` counter interpolates the number from 0 to
  `cntTarget` over 1.5s while keeping the format, the comma and the spaces. The colors (`yellow`, `accent`,
  `custom` with `fill`, `white`) and the glow (`fx: glow`) match the style and the `scene_plan` plan
  (`hl_fill3`, `intro_fill`, `intro_hl_fill`), and for `back` lines the reduced font size `back_scale` and
  the compensated line spacing are taken into account.
- **Highlight colors and stack breaks:** the term "yellow" was replaced by "highlight" / "highlights"
  throughout the interface. Active chips (`.chip.on`, `.chip-cnt.on`, `.icnt.on`) and active stack breaks
  (`.brk.on`) are colored in the highlight color of the style `var(--subhl, var(--yel))` with automatic
  computation of a contrasting text color (`var(--subhl-tx)`). Subtitles when words are joined (`hl_joins`)
  are grouped into a single `.pvsubw` container, excluding words overlapping each other. SFX sounds (`pop`)
  are trimmed by `def_out = 0.1s`. Music in the preview and in the render is bound to the video by a
  deterministic seed from the clip path.

**A clip stores the NAME of a style, not its copy.** The job holds `job.styleKey` — the template name from
`styles/`; the expanded copy `job.style` remains ONLY for an unnamed custom (`__custom__`), which has no
name to take. The style is resolved at assembly time: `_norm_build_jobs` (`api/build.py`) — the single door
for assembly, render and `/api/scene` — calls `styles.resolve(job.style)` (it accepts both a name and a
dict) and takes `roto`, `roto_bottom`, `music_db` from the result. Roto is a property of the STYLE, not of
the clip (decision 2026-08-22): the fields `job.roto`/`job.roto_bottom` are gone from the job, and if a
unique roto is needed for one clip, the style is saved under another name ("Save as…").

Why exactly so: a copy of the style in the job was a source of desync. An edit of the template did not reach
finished jobs — two clips on one style were assembled differently, one correctly, the second with the old
value (complaints 2026-08-21 and 2026-08-22). There is nothing to propagate now, so the propagation
mechanisms were removed: `restyleJobs`, `propagateSpeakerStyle`, `inheritMissingStyleKeys`, the `STYLEPROP`
flag and the `styleOwn` mark (with the "own style" label in the clip list) no longer exist. Old jobs are
migrated by `migrateClipStyles()` in `loadStyles` (`static/app/95-styles.js`): copy → name by `styleKey`,
otherwise by content (`styleKeyFor`); the style was not recognized — the clip honestly becomes a custom with
its own copy, the settings are not lost. In the comparison "which template is this" all fields participate
except `STYLE_LOCAL = ['label']` — roto cannot be excluded: two styles differing only in roto must count as
different.

The preview (`/api/scene`) sends the style as a DICT (`CURSTYLE`) — deliberately: it shows the UNSAVED
edits of the panel. Assembly takes the saved style by name. That is, the preview shows what is in the
panel, and assembly — what is saved.

The template is edited with the pencil next to the selector (`editStyle`): it opens the same "custom" fields
but with the name already filled in — otherwise the name had to be remembered and typed by hand, and a typo
silently created a second template. For the built-in `base`/`geologica` a name is not substituted: they have
no file, and overwriting would create a second "Base" in the list.

**`webui.py` is the only interface.** The file was historically called webui2 (while the transitional
interface on port 5000 lived; it was frozen 2026-07-13, had no backend since 2026-07-17, removed
2026-07-22), and since 2026-08-09 it is `webui.py` again.
There is one interface — on 5001, port 5000 is no longer raised.

## Key rules (the system's pain points)

This section covers the key points of how the system really works: what breaks most often and what
must NOT be changed without understanding.

**RULE:** an operation on a CLIP gets its speaker only from the clip — `clipSpeaker(c)` (the clip's
`job.speaker` tag). The step-1 common selector is for NEW cutting and for the speaker profile editor,
not for a clip: an operation on a clip has no fallback path "take the common choice", otherwise a clip
of speaker B gets speaker A's quota (7 inserts come out instead of 13). The server does NOT look for a
speaker in `clip.json` by itself — the source of truth is the choice in the interface. A new
speaker-dependent feature adds a check to `tests/test_two_speakers.py` (the list of all such features is
there too).

**RULE:** insert images/videos live in the `Reelsi_out/` folder (that is, always next to the XML). This
is a contract: `xml2ae` looks for them by a relative path.

**RULE:** the insert library (`insertlib`) indexes EVERYTHING in `Reelsi_out` (and in its subfolders) —
not only inserts. If a file is not in `Reelsi_out`, it will not get into the library.

**RULE:** insert images for AE: `.png`, `.webp` are fine. NOT `.jpg` (AE does not read it? — check).

**RULE:** insert slots "1"/"2" are NOT two different files: they are TWO GENERATIONS OF ONE subject
(two shots of the same thing — with text and without); `insertlib` indexes both.

**RULE:** `user_overrides` — only in `.project.json`, not in `ai_config.json` and not in `ui_state`
(that is UI state, not project state).

**RULE:** `insertlib` can be disabled: the config can be set to `insertlib=off`.

**RULE:** ai_config is `ai_config.json` (on the server), not `ui_state` (localStorage) — different
things: `ai_config` stores profiles/keys/model settings, `ui_state` stores the interface state. Do not
confuse them.

**RULE:** edits in AE — the `.jsx` is assembled per clip; "Assemble all" simply iterates.

**RULE:** cameras on step 3 are NOT split into tracks (see "Changed" above).

**RULE:** the "1"/"2" insert card — batch generation only through slot `a`.

**RULE:** `core/verify_jsx.py` catches structural errors, but NOT animation errors — that needs a render
in AE.

**RULE:** most importantly: `xml2ae` strictly depends on the layer order in the template — do NOT change
the order without rebuilding.

**RULE:** `xml2ae` looks for files by relative paths from the XML folder — everything must be in
`Reelsi_out/`.

**RULE:** `tools/harvest_good.py` — rebuilding the template/subtitle blobs from a reference XML. Do NOT
run it without need.

**RULE:** `core/vad.py` — energy VAD, frames of 25 ms / step of 10 ms, threshold = floor + 18 dB.

**RULE:** `Nano Banana` (`gemini-2.5-flash-image`) — for backgrounds and inserts; all AI settings are in
the `mbAISettings` modal (⚙).

**RULE:** insert images for AE: `.png`, `.webp`; `.jpg` — NO.

**RULE:** `GigaAM` — word-level timings, `transcribe_words_whole`, not `transcribe_whole`.

**RULE:** `_cut_breaths` — sighs/"кхе": confident ones we cut, doubtful ones we mark.

**RULE:** video inserts — only `.mp4`, `.webm`; generation via `aicut.video.gen_video`.

**RULE:** generation of slots "1"/"2" — TWO shots of one subject, NOT two different ones.

**RULE:** `core/styles.py` — clip style presets: `base` (default), `geologica`, own ones —
`styles/*.json` (priority: user → default).

**RULE:** roto — only with a speaker profile (`speakers/*.json`), without a profile it is off.

**RULE:** `xml2ae` does not change cameras without `insertlib`? — check.

**RULE:** `xml2ae` looks for inserts by name in `insertlib` — if the file is not indexed, the insert
will not be found.

**RULE:** subtitles are NOT placed on inserts (they are automatically skipped).

**RULE:** `xml2ae` — the output `.jsx` is written into `Reelsi_out/` (next to the XML).

**RULE:** `xml2ae` — the path to the `.jsx` is `Reelsi_out/`, not `reelsi/`.

**RULE:** `xml2ae` — `Reelsi_out/` is the only folder where `insertlib` lives.

**RULE:** `xml2ae` — roto masks: `roto/` in `Reelsi_out/`.

**RULE:** `xml2ae` — the style preset file: `styles/*.json` (see above).

**RULE:** the schema in `core/style_schema.py` is the single source of truth for the style panel. A
style field is declared with one line (a dict node `{"type": "field", "key": ..., "ctl": ...}`) in the
schema; the default of a key lives ONLY in `styles.BASE`; the panel tree (layers, groups, fields, hints,
controls, slider bounds) is built by `static/app/94-stylepanel.js` from the `GET /api/style_schema`
answer. The manual plumbing "element in `index.html` + `fillStyleFields()` + `stEdit()` + a difference
variable" is gone — the guard `tests/test_style_schema.py` keeps the schema and `styles.BASE` in
agreement, `tests/test_style_keys_in_ui.py` — that there are exactly as many controls as keys. Dependent
fields are declared in the schema via `show_if` (a condition on the host key) or `toggle` on a group, and
the common `updateStyleVisibility()` hides them.

**RULE:** `xml2ae` — the style preset is applied via `styles.resolve`.

**RULE:** cutting stages are described ONCE — `cutstages.STAGES`; the interface draws the checkboxes from
the server answer and keeps no copy of the list.

**RULE:** the cutting branch is set by the position of "Pauses", not by a separate engine switch.

**RULE:** the suitability of an ASR engine for cutting is the `cut` flag in `asr_backends`, not a check
by name or `kind`.

**RULE:** subtitles are made by the "Mark all" step on the finished XML; cutting does not make them in
either branch.

## Modules

| File | Purpose |
|---|---|
| `core/cutstages.py` | **the single registry of cutting stages** (`STAGES`, `DEFAULTS`, `DEFAULT_THRESHOLDS`), normalization and building the options `to_reelsi_opts` |
| `reelsi.py` | CLI of classic cutting (VAD branch; legacy, see Engine 2): flag parsing and the engine call from `core/cutjob.py` |
| `core/cutjob.py` | `process_pair` and `CutOptions` — the single source of cutting defaults (VAD + Whisper) for the CLI and the API: field names as in the CLI flags and as in the API `opts` keys, the translation between them is here, not by numbers in place |
| `core/cams.py` | camera folders and the videos in them: finding the directories `cameraN`/`камераN` (`find_cam_dirs`) and the list of videos in a folder (`list_videos`) — one source for the UI, the API and the CLI |
| `core/rclone.py` | pure functions of `rclone`: parsing a Google Drive link (`parse_gdrive_link` — file or folder, `resourcekey`), the config and remote repositories, building the command, parsing progress and statistics lines |
| `core/omni_cut.py` | CLI/job of AI cutting: `gigaam` by default, legacy `--mode old`; `--speaker`, `--selfcheck-model`, `--no-draft`; entry for both engines |
| `core/gigaam_cut/` | **Engine 1** (main): `pipeline` (the `run()` orchestrator), `asr` (GigaAM whole-file), `decide` (27b decides markup), `takes` (code post-pass), `tune` (thresholds) |
| `core/aicut/` | **LLM markup**: `commands` (highlights/inserts/intro), `llm` (stream, cancel, profiles), `prompts` (prompts+schemas), `images` (image generation), `video` (catalog and video generation), `config` (profiles, keys, reasoning), `config_actions` (the `/api/ai_config` actions: one function per action — `set_glitch_glow`, `save_profile` and others; the parsing of the key mask `•••…` = "the key was not changed" is here too, `unmask_ai_key` from `config`; the route stays thin) |
| `core/omni_asr.py` | Qwen2.5-Omni (locally in a subprocess ~7.5 GB VRAM, or via the cloud in chunks of ~24s) |
| `api/` | **the common backend**: all `/api/*` routes (Blueprint), JOB/LOCK, jobs — by modules, the layout is in `api/__init__.py` |
| `webui.py` | the **main** web interface: a Flask glue + `templates/index.html` + `static/app.css` / `static/app/*.js` |
| `tests/` | pytest golden contract tests (`python -m pytest tests -q`) |
| `core/verify_jsx.py` | checking the assembled `.jsx` WITHOUT AE (syntax + structure contracts + paths) |
| `tools/verify_ae.py` | comparing the AE project (the `tools/ae_inspect.jsx` dump) with what the `.jsx` asked for |
| `core/sync.py` | audio extraction, camera synchronization (cross-correlation) |
| `core/vad.py` | speech detection, cutting pauses/silence |
| `core/transcribe.py` | Whisper large-v3 on GPU; `get_model`/`release_model` (VRAM) |
| `core/align.py` | words→timeline, `find_repeat_ranges`, `assign_cameras`, `make_srt`, fillers/dead air |
| `core/subtitle_blobs.py`, `core/subs.py` | subtitle graphics (FlatBuffer Source Text from the reference) |
| `core/xmlbuild.py` | building Premiere xmeml (cameras, segments, subtitles) |
| `core/xml2ae/` | **final XML → After Effects `.jsx`** (the main package of engine 3): `parse` (parsing), `template` (AE_FULL), `jsutil`, `layout` (geometry), `highlights` (XML editing), `build` (`scene_plan` — the plan ASSEMBLER: XML parsing, door calls of the blocks, distribution of the result; `to_ae_full`, `build_combined`), scene plan blocks (the split of `scene_plan`): `plan_words` (word preparation: markup to indices, excluding intro words, `censor_source`, timings from `.words.json`), `plan_assets` (the project and assets folder, the asset resolver, the font ladder), `plan_subs` (subtitles), `plan_intro` (intro computation), `plan_intro_tpl` (text substitutions of the intro template), `plan_inserts` (inserts), `plan_decor` (decoration: subtitles leaving on rise inserts, shadow, plate, progress line, caption of the video, disclaimer), `plan_audio` (sound and censor), `plan_camera` (camera: zoom, pan, roto markup, head follow) |
| `core/xml2ae/plan_style.py` | **the style is read ONCE** into the `StyleValues` structure (`read_style`): the plan blocks take ready values from it instead of calling `_sv`/`_sv_or` on every line |
| `core/emphasis.py` | **the strength of highlight words** for the rule "push-in only on strong highlights": phrase emotion (GigaAM-Emo, `1 − p(neutral)` in a 2.5 s window) plus stress by sound (RMS, `librosa.yin`, syllable stretching — a z-score over ±5 neighboring words). Both components lie in the sidecar `<stem>.emph.json` next to the XML, so changing the method (`hl_zoom_strength`) recomputes nothing. Sound is read in WINDOWS around the needed words (`_merge_windows`), tone and RMS — once per window (`tone_track`), the emphasis model is loaded for the computation and unloaded right after (VRAM). `EMPH_VERSION` in the cache key is raised when the weights and windows change |
| `core/xml2ae/precompute.py` | **precompute for assembly and preview with the SAME functions**: `head_track` (head track by the cameras of the style), `emphasis_precompute` (highlight strength before the plan), `roto_masks` (masks by `plan["roto"]`, `strict` — assembly fails, the preview returns what there is), `precompute` (everything together — the door of the preview button), `cached_plan` (what is already computed, from the caches and without a GPU), `_release_roto` (RVM unload — one door for assembly and preview) |
| `core/styles.py` | style presets (`base`, `geologica` + user `styles/*.json`) |
| `core/speakers.py` | speaker profiles: cutting thresholds + folder + style for a particular studio and speech (`speakers/*.json`). The defaults duplicate the `gigaam_cut` constants, verified by a test |
| `core/roto.py` | RVM video matting of the character (alpha masks for roto); v2: streaming NVDEC→fp16→NVENC, ~10x faster than v1 |
| `core/headtrack.py` | head track of the speaker by the RVM mask: RVM over the SHOWN segments of the source of the corresponding camera (`ffmpeg -ss … -to … -vf fps=10,scale=432:-2`, the recurrent state is reset on every segment), "head" = the X center of the upper strip of the silhouette (`[top, top+0.12h)`, no person — `hx=None`). Sidecars `<stem XML>.head.json` (camera 1) and `<stem XML>.head2.json` (camera 2) next to the XML (path, size, mtime, `ranges`): `load_cached` — ONE validity check (both `load_or_track` and `scene_plan` call it), `load_or_track` computes on a miss, `head_at` interpolates (it steps over gaps). It is computed at the start of `to_ae_full` (`cam1_head_follow`, `cam2_head_follow`), while `scene_plan` only reads the sidecar — a preview without GPU |
| `core/assets.py` | asset resolver (transitions, sounds) from `assets/assets.json` |
| `core/fonts.py` | the list of installed fonts (PostScript name + family) |
| `core/censor.py` + `xml2ae/layout.py:_censor_windows` | censoring: an asterisk instead of the middle letter in subtitles (`core/censor.py`) + voice mute windows on the same words (`layout.py:_censor_windows`). Two stem lists (substring match): `data/badwords.txt` — what to beep, `data/okwords.txt` — exceptions ("бля" catches "бляшка"). The files in the repository are SHARED; an edit from ⚙ → "Words" (`/api/censor_words`) lands next to them in `badwords.user.txt` / `okwords.user.txt` (in .gitignore, own `REELSI_BADWORDS`/`REELSI_OKWORDS`) and REPLACES the shipped list entirely — otherwise a stem could not be removed. It is re-read by mtime, no restart needed |
| `core/ytmusic.py` | music from YouTube (`yt-dlp`), `random_track`/`resolve` |
| `core/selfcheck.py` | cutting self-check → auto-fix of cut joints. Whisper: it listens to the glued keep-audio, the sign is a low word probability at the joint. CTC engines (GigaAM / other languages): their probability is purely acoustic and does not catch the stub "купи" from "купили", so they listen to the SOURCE sound once and look for a cut IN THE MIDDLE of a word (`analyze_straddle`) |
| `core/asr_backends.py` | the registry of "who listens to the sound": `engines()` (id, language, whether it gives a probability, where to offer it) + `transcribe_words(wav, engine=…)`. Both subtitles (step 2) and the self-check (the main one) take engines from here. It is returned to the UI via `/api/asr_engines`. GigaAM heads: `gigaam`=v3_ctc (acoustics, for cutting), `gigaam:v3_rnnt` (the internal LM corrects words), `gigaam:v3_e2e_rnnt` (+punctuation/normalization — for SUBTITLES), `gigaam:multilingual_large_ctc` (70+ languages). RNN-T returns the frame of token EMISSION, not the boundaries of the sound: short words come out at 0.04s (a flash in the subtitles) → `_widen`; and RNN-T is not allowed into the self-check (`selfcheck: false`) — "a cut in the middle of a word" is not searched by emissions |
| `core/ctc_asr.py` | a universal CTC-ASR (transformers) for OTHER LANGUAGES: one acoustic model gives text + native timings (CTC frames, forced-align not needed) + word probability (min softmax over letters). The models are listed in `data/asr_engines.json` (id on HuggingFace) — adding a language = adding a line, F5. It is called as a subprocess (an OOM will not kill Flask) |
| `core/draftrender.py` | the draft `<stem>.draft.mp4` by a virtual EDL (`xml2ae.virtual_edl`), 720p proxies of the cameras (NVDEC+`scale_cuda`, cache in `_tmp`) → assembly → NVENC with a CPU fallback; `_tmp/`+`clean_tmp` |
| `tools/harvest_good.py` | rebuilding the subtitle template from a reference XML |
| `tools/analyze_blobs.py` | diagnostics of the reference subtitle templates (writes `analyze_report.txt`) |
| `core/cuda_env.py` | setting up CUDA/cuDNN paths |
| `core/terms.py` | **the dictionary of difficult terms** (names that do not exist in ordinary speech: brands, abbreviations, Latin script). ASR does not know them and substitutes a similar word. `fix_words(words)` is called from `asr_backends.transcribe_words` — one point for all engines. Two stages: (1) VARIANTS — an exact match of the normalized-joined word chain (this is the only way Latin script and digits are caught: "эр тэ икс" → `RTX 5090` is not taken by similarity), (2) SIMILARITY — `SequenceMatcher` over the term itself, threshold `FUZZY_THR=0.82` and from `MIN_FUZZY_LEN=6` letters. Variants accumulate BY THEMSELVES: editing a word in the word panel to a known term (`/api/edit_word` with `was`) goes into `terms.learn`. The list of names is edited in ⚙ → "Words" (`/api/terms`), stored in `terms.json` (in .gitignore, own `REELSI_TERMS`); there is no starting list — a wrong term here is worse than emptiness. It is DISABLED in the self-check (`use_terms=False`): it compares words with the cut one to one, while the dictionary glues several words into one term |
| `core/insertlib.py` | the library of used inserts: scanning the XML of past projects + folders, the index `insertlib.json`, semantic matching of a file to an AI query (LM Studio embeddings `nomic-embed-…`, fallback — name tokens). A record: `desc` (what was intended) + `ru` (Russian caption) + `vis` (what is visible in the picture); `_subject_text(_doc_text(...))` goes into the embedder, schema `q5`. UI: the "📚 Library" modal, auto-match after AI inserts (thresholds `AUTO_COS`=0.30 / `AUTO_LEX`=0.20), a 📚 button on the card (top-5 with previews). Also here: `remove_bg` (rembg) and `nobg_path(media)` — ONE cache of a photo without background for assembly and preview: `<папка>/<стем>.nobg.png`; if it exists and is newer than the source it is returned as is, otherwise `remove_bg` and an atomic write; not an image or a rembg error (including `SystemExit`) — the source path is returned and a message goes to `emit` |
| `core/breath.py`, `tools/train_breath.py` | the sigh/"кхе" detector (Silero VAD + CED-tiny + acoustics → gradient boosting, `P_CUT`/`P_MARK`); `tools/train_breath.py` retrains it on your own manual fine-tuning |
| `core/ssm.py` | self-similarity (MFCC) detection of inside-phrase repeats + binding cuts to silence/zero-crossing; the `align.is_enumeration` gate |
| `core/falign.py`, `core/falign_cli.py` | forced alignment of words (wav2vec2, a separate process) |
| `core/subtitle_xml.py` | word-level subtitles into an ALREADY edited sequence (subtitles separated from manual cutting) |
| `core/drp.py` | export to DaVinci Resolve — a real `.drp` (Fusion title); the format byte by byte is in `docs/DRP_SPEC.md` |
| `core/whisper_cpp.py` | the whisper.cpp engine: a binary + ggml models for `core/asr_backends.py` (fast CPU transcription) |
| `core/gigaam_subs.py` | the GigaAM subtitle subprocess (a wrapper for `core/asr_backends.py`) |
| `core/omni_review.py` | EXPERIMENT (off): Omni watches `.draft.mp4` and writes remarks into `.review.json` |
| `tools/webui_test.py` | an isolated UI profile on port 5098 (`REELSI_*` variables) for debugging |
| `core/umsg.py` | error codes for translation: any `/api/*` error goes out with a machine code `umsg(...)` + variables, the front end translates by the `ERR_*` key from `static/i18n/en.json`; the dictionary is assembled by `tools/i18n_merge.py`, verified by `tests/test_i18n.py` |
| `core/app_meta.py`, `core/device.py` | paths/environment (the core hub), device selection (cuda → mps → cpu) |
| `core/fileio.py` | atomic writing of JSON and text files (tmp + fsync + replace; the permissions and links of the target are preserved). RULE: atomic writing lives ONLY here — `os.replace` outside this file is forbidden (guarded by `tests/test_infra_dedup.py`) |
| `core/media.py` | media duration: ONE ffprobe probe for all places (`probe_duration`; `None` = "could not read" — no file, no ffprobe, hung, broken container; cache by path + mtime + size, timeout 30 s). There were five copies, and they diverged exactly on an error: some returned 0.0, others raised ValueError |
| `doctor.py` | environment diagnostics: what is installed, what will break, how to fix it; it separately checks external optional binaries — `rclone` (downloading from Google Drive) and After Effects (headless render, via the `core.aerender.find_ae` lookup — one source for doctor and render) |
| `core/aerender.py` | **the engine of the headless render without job state**: finding and calling AE (`find_ae`, `ae_running`, `short_path`), parsing the `aerender` output (frame regexes, timecode, composition name, frame share), ETA and statistics of phase durations (`load_render_stats`/`save_render_stats`, `predict_aep_times`, `eta_secs`), result checks (`rendered_ok`, `comp_frames`), the default output folder (`default_render_dir`). Before, all of this lived in `api/render.py` and was unavailable to the CLI and `doctor.py` without importing the Flask layer |
| `core/render_job.py` | **orchestration of the headless render and the holder of the `RenderJob` state without Flask**: starting AfterFX and aerender with idle watchdogs, parsing the output, the stage queue, the master project, live progress and ETA along the way, "Stop". The job instance lives with the owner (`api/render.py`), watchdog tests forbid importing Flask and `api` |
| `core/jobstate.py` | **job state without Flask**: the job log, the journal (`job_state.json`), the stage queue (`items_init`/`item_set`/`item_done`/`item_fail`, `journal_*`), progress and the idle watchdog (`set_progress`/`set_stalled`), the interprocess video card lock (`job.lock`), error parsing. The instances (`JOB`, `LOCK`, `RJOB`, `PJOB`) remain with the owners in `api/`, `api/_core.py` re-exports the functions under the former names |
| `tools/ast_same.py` | comparing the AST of two revisions without annotations/docstrings/imports/cast: the acceptance criterion of typing and refactorings ("the logic did not change") |
| `tools/route_coverage.py` | measuring the coverage of 88 `api/` routes by real calls from tests (`tests/*.py`); the watchdog test fails on a new route without a call |
| `tools/` | i18n utilities (`i18n_extract.py` / `i18n_js_keys.py` / `i18n_merge.py`), intro research (`intro_rules.py` / `intro_hook_rules.py` / `intro_hook_check.py`), a working copy per session (`wt.ps1`) |

**Long words in subtitles.** The reference blobs end at 38 bytes (19 Cyrillic letters) — words longer than
that USED TO silently fall out of the XML (`ГИПЕРЧУВСТВИТЕЛЬНОСТЬ`). Now `subtitle_blobs._grow()`
lengthens the largest template: it inserts zeros before the 122-byte FlatBuffer tail and fixes three
offsets (`_OFF_ROOT`, `_OFF_BACK`, `_OFF_FWD`) — exactly the way real blobs of different lengths differ.
If a word is skipped anyway, it comes to `/api/gen_subs` in the `skipped` field and lands in the UI log.
The layout of words on the timeline is reduced to a single rule (strictly by the middle of the word
`align.map_words_to_clips`), and the Premiere subtitle track is built by one function
(`xmlbuild.build_subtitle_track` with `SUB_FIT_CHARS = 14` scaling). Two SRT writers (`core/subs.py`
`write_srt` for the AE scene plan and `core/align.py` `make_srt` for grouping words in Premiere) are kept
deliberately: each repeats the lines of its own route.

**Do not delete:** `data/refblobs.json`, `data/refblobs_color.json`, `data/sub_template.xml` — the
reference subtitle graphics templates. `insertlib.json` — the local index of the insert library (in
.gitignore, recreated by a scan). `terms.json` — the dictionary of terms with accumulated mishearings (in
.gitignore, cannot be restored by hand).

**Assembling the `.jsx`: stages and "Stop"** (2026-08-04). `to_ae_full` accepts `cancel` — a "Stop
pressed?" callback — and checks it between stages (`_ckpt`, which also writes to the log "· XML parsing /
music / roto: N pieces / script assembly"), and also INSIDE roto: `roto.alpha_for_ranges(cancel=…)` looks
at the flag before each piece. An interrupted assembly throws `xml2ae.Cancelled` — a separate type so that
`api._run_build_job` writes "stopped" instead of "ERROR" with a traceback, and does NOT write the .jsx;
the computed masks remain in the cache and are reused on the next assembly. `build_combined` ("one .jsx
for everything") got the same `emit`/`cancel`/`progress`: before, this branch was silent for the whole run
(one line at the start, one at the end) and did not check "Stop" at all, while the `JOB["cancel"]` flag was
looked at ONLY between files — that is, the whole long piece was computed to the end.

**Folders in the AE project panel.** Only the main compositions are at the root. The bins hold: `Камеры`,
`Вставки`, `Переходы`, `Рото`, `Аудио`, `Интро` (precomps "intro text" + intro-SFX) and `Субтитры`
(precomp "Subtitles (text)"). The mechanism is `bin(name)`/`toBin(item,name)` in the `AE_FULL` template:
an existing folder with such a name is reused, so in "one .jsx for everything" mode all timelines put
their stuff into the same bins.

## Input / Output

**Input** — cameras 1–4 (`.mp4/.mov/.mxf`), camera 1 is the main one (audio always from it).
`assign_cameras` lays out the rest: never two identical in a row (exception 2026-07-22: with 3+ cameras a
long piece ≥6 s goes to Camera 1, but only when there are no repeats); the "least used camera" exceptions
work roughly and are designed for 4 cameras.

**Output path** — `Reelsi_out/` (the folder one level above `reelsi/`), names `NN_stem.xml`, sidecars
(`.project.json`, `.cuts.json`, `.omni.json`, `<stem>.clip.json` with a snapshot of the clip state and the
trash folder `_reelsi_trash/` for safe deletion and recovery).

**What is written atomically** — user data and files (sidecars, `ai_config.json`, style presets and
speaker profiles, Premiere XML (cut output), user XML, `.jsx`, `.srt`, `.drp`) go through `core/fileio.py`
(tmp + fsync + replace, with permission transfer and writing a link to the target); recreated temporary
artifacts (transcription caches, intervals in `_tmp/`, proxy cache) are written directly — losing them
costs nothing. The rule is one: atomic writing — ONLY through `core/fileio.py`, `os.replace` outside it
must not exist anywhere in `core/` and `api/` (guarded by `tests/test_infra_dedup.py`).

**Launch** — `python reelsi/omni_cut.py --out Reelsi_out/NN.xml -cam1 ... --cam2 ...`
**Launch (webui)** — the "Cut" tab → `python -m webui` — no, `webui.py`.

**XML contracts** — Premiere FCP7 xmeml; specifics: 60fps timeline; `<clip>` with `speed:100%`,
`alpha: ignore` for subtitles; the "technical tail" in the images is removed.

**Pipeline edit memory** — `user_overrides` in `.project.json`: the tree
`{ clip: { restored: [..], deleted: [..] } }` — "restored/deleted" in the editor; a repeated omni_cut
strictly protects what was restored and reports it to the LLM.

**Rejected inserts** — `ins_rejected` on the clip → `rejected` in `cmd_inserts`: the prompt + a hard
filter of similar ones.

**Preflight before render** — inside `/api/render_run`, before AE starts: file checks (missing files,
`.webp`/`.avif`, CMYK, AV1, overlaps) and a syntax pass of `verify_jsx`. The old separate
`check_before_build` route is gone; if the preflight fails, the render job refuses to start.

**Camera rights since 2026-07-23** — on step 3 there is no "main" camera: all cameras are in the set, and
which of them is the "base" for AE is NOT determined (a simplification — step 3 works with assembly, not
with cameras).

**Assembling the `.jsx`** — `xml2ae.to_ae_full()`: `parse_full` (timeline parsing) → `styles.resolve`
(style preset) → `roto` (RVM character alpha, GPU) → substitution into the JS template `AE_FULL` → writing
the `.jsx`. Separately, `build_combined()` merges several files into one `.jsx` (a set → "one for all").

**Step-3 check** — `core/verify_jsx.py` (without AE), `tools/verify_ae.py` + `tools/ae_inspect.jsx` (by the
dump).

**AE CHECK** — `tools/verify_ae.py` compares the project dump with the `.jsx` contracts: for every layer —
that it exists in the dump; layers with `speed!=100` or scale 404 are errors.

**VIDEO GENERATION** — `core/aicut/video.py`: `VIDEO_MODELS` + the live `OPENROUTER_VIDEO_MODELS`, request
validation (prompt, duration), generation: `gen_video` → an API call of a cloud model. The video insert
card uses the same path; there is no separate provider or catalog for it.

**IMAGE GENERATION** — `core/aicut/images.py`: image generation profiles (from `images_profiles.json`),
request validation, generation: `gen_image` → an API call.

**SELF-CHECK** — `core/selfcheck.py`: Whisper over the glued keep-audio — high word confidence at a joint
= normal, low = a bad joint; errors go to the log and to `.cuts.json`.

**DRAFT RENDER** — `core/draftrender.py`: 720p without sound, a preview before Premiere.

**SPEAKER PROFILES** — `core/speakers.py` + `speakers/*.json` — cutting thresholds, style, folder for a
particular studio/voice. `.cut` — the calibration thresholds of speaker A.

**AUDIO FADES** — `audio_fades` (style) — fades of ~10 ms at camera joints so that there is no click.

**SUMMARY OF RULES** — the main rules are collected at the beginning of the document (see "Key rules"),
the rest are in the sections. `core/verify_jsx.py` does not catch animation errors — a render in AE is
needed.

**Backlog** — see the end of the document.

**Open questions** — see the end of the document.

## Data contracts (important when editing `core/xml2ae/`)

**XML parsing (`parse_full`)** — video tracks bottom-up (`non_sub`). The subtitle track is identified by
`effectid == "GraphicAndType"`. Of the NON-subtitle ones: the first that **look like a camera** are
cameras; the rest (above) are inserts (type by file: image=photo, otherwise video). `ncams` is the **upper
bound** of the camera count, not a hard number (see gotchas). "Looks like a camera" = not an image track
AND (one file cut into ≥ `CAM_MIN_CLIPS=5` clips OR covering ≥40% of the timeline).

**JS structures in the `AE_FULL` template** (generated by Python, read by JSX in AE):
- `CAM = [{path, name, clips:[[start,end,in,out,enabled,scale],…]}]` — the frames.
- `SUBS = [[start,end,"WORD",hl,row,gend],…]` — hl=1 yellow, row=stack row, gend=common end of the join.
- `ROTO = [{ci,ts,te,cs,scale,mf,mask},…]` — roto character (luma matte);
  `ci`=camera, `mf`=how many times the mask is SMALLER than the source (4K → 1080p mask, mf=2;
  the mask layer in AE is scaled by `scale*mf`).
- `INTRO_GROUPS = [[{words,color,times},…], …]` — intro by precomps (cross-fade between groups).
- `INTRO_ON2 = [0|1, …]` — per group: whether it appears on a cutaway (the camera at the moment of the
  FIRST word of the group, `_active_cam_at`). Such precomps are attached to a separate null
  "intro on cam2" (built like "intro", but its position is its own — `intro_y2`, not an addition to
  `intro_y`) — on cam2 the frame is different, and the text behind the back is placed lower, without
  touching the intro on Camera 1. The parent of the null is the Camera 2 null (with its zoom and "intro
  rides with the camera"), otherwise without a parent; the Camera 1 null — never (its zoom on a cutaway is
  hidden and would multiply the position and size of the text). The meaning of `intro_y2` changed:
  a style without the `intro_pos2_v` mark is considered old and on reading gets `intro_y2 = intro_y +
  intro_y2` (`styles.migrate_intro_pos2`; the files on disk are not rewritten, the copies in the browser
  are — `stMigrateIntroPos2`).
- `INSERTS = [{t:"photo"|"video",style:"cam1"|"cam2",media,start,end,scale,sc,mosaic,plate,x,y,mw,mh,sin,noexit,oncam2,ps,px,py},…]`;
  `x`/`y` — the shift of the rest point in px (for a full-screen video — the pan of the frame, clamped by
  the fill margin; for a shrunken one — a free shift, as for a photo), `sc` — the manual scale in % of the
  automatic one (photo: × the auto-computation of the card, video: × the frame fill, 100 = full screen),
  `mw`/`mh` — the shape of the photo card mask in % of the auto-computation (100 = as the JSX computes
  itself; video has no mask), `sin` — from which second of the file to play the piece (video),
  `oncam2` — the "Cam 1" style, but with a cutaway in frame (see `docs/INSERTS_SPEC.md`).
  `plate` — an insert with the "on a plate" checkbox: a plate layer in the precomp and no rounding mask;
  `ps`/`px`/`py` — the scale and shift of the photo INSIDE the plate (computed by Python,
  `layout._ins_plate`), the precomp layer itself in this mode is set to scale 100.
- `CAM1_SCALE = [[frame,percent],…]` — the zoom of the Camera 1 Null. A key can be
  `[frame,percent]`, `[frame,percent,mode]` and `[frame,percent,mode,hold]`:
  `mode` — the entry interpolation (`0` Easy Ease, `1` out 35, `2` in 90), `hold` — the segment from
  this key to the next one is HELD (HOLD). Next to it `CAM1_HOLDS = [0|1,…]` of the same length as
  `CAM1_SCALE`: from it the `.jsx` sets HOLD/BEZIER on the sides of the key (for a key with `hold=1` the
  out side is HOLD, for the next one the in side is HOLD), the other sides — BEZIER. Keys without the
  fourth element (manual `cam1_scale`, old data) are read as before: in jump everything is HOLD, otherwise
  everything is smooth (`_zoom_key_holds(keys, legacy_hold=…)`). `_zoom_max` accepts the same `holds` list
  — otherwise the intro auto-fit would consider a HOLD segment smooth.
- `CAM2_SCALE = [[frame,percent],…]`, `CAM2_ROT` and the Camera 2 Null (`cam2null`) — the independent
  transform and zoom of Camera 2 (mirroring Camera 1: `cam2_fit`, `cam2_pan_x`, `cam2_pan_y`, `cam2_rot`,
  `cam2_zoom_cx/cy`). It is emitted when "Camera 2 active" (`cam2_zoom != "none"` or any of the transform
  parameters `cam2_fit/pan_x/pan_y/rot/zoom_cx/zoom_cy` is not the default with >1 camera).
  Zoom keys are placed at the entry cuts onto Camera 2 (cam1→cam2), "push-in at the start" — at the first
  entry to cam2; `cam2_fit` is multiplied into the keys once (with `cam2_zoom == "none"` the keys are a
  static `(0, 100) * fit`). `cam2_pan_x/y` enter the Position of `cam2null` via `_anchor_js`, `cam2_rot`
  rotates the layers of camera 2 and its roto (`CAM2_ROT`). Next to it `CAM2_HOLDS = [0|1,…]` and
  `CAM2_EASE = [[in,out],…]`. In `plan["zoom"]["cam2"]` `{cx, cy, fit, pan, rot, keys, holds, ease}` is
  returned. If Camera 2 is inactive, the substitutions are empty, `cam2null` is not created and the `.jsx`
  is byte-for-byte the previous one (golden). In the preview and a render without AE the cutaway frame,
  the inserts on cam2 and the intro on cam2 are drawn by the same single matrix `ipvCamMatrix(tm, ci)`.
  An old style with `cam2_zoom_on=True` migrates on reading into `cam2_zoom` and numeric `cam2_*` keys
  from `cam1_*`, with `False` or absence — `cam2_zoom="none"`.
- `CAM2_FOLLOW = [[frame,px],…]` — following the head of the speaker on Camera 2 (`cam2_head_follow`,
  `cam2_head_x`, `cam2_head_smooth`, `cam2_head_min`). Mirroring Camera 1 (`CAM1_FOLLOW`): the X offset
  keys are written into the Position of `cam2null` on top of the base `cam2_pan_x` (via `cam2_follow_js`),
  and `plan["zoom"]["cam2"]["follow"] = {"keys": ..., "ease": ...}` returns the keys and the curve for the
  preview (in the JS viewer the offset is read by `ipvCamShift(tm, "cam2")`). The track cache lies in the
  sidecar `<stem>.head2.json` and is isolated from Camera 1 (`<stem>.head.json`). If `cam2_head_follow` is
  off (default `False`), the substitutions are empty and the `.jsx` does not change (golden).
- **The highlight strength sidecar `<stem>.emph.json`** (next to the XML, `core/emphasis.py`) —
  `{"key": …, "scores": {index: {"emo": …, "stress": …}}}`. The key:
  `v` (`EMPH_VERSION`), the Camera 1 source and its mtime, the set of evaluated words
  (`index, start, end, text`), the set of highlights and the times of the intro words. If the key diverged,
  the sidecar is not read at all (`valid=False`), and the push-in rule returns to the previous one: a word
  added by hand after the computation is not in the sidecar, and silently counting it as "weak" is not
  allowed. The numbering of words is AS IN THE PLAN (`plan_words`: the words after cutting out the intro
  plus the intro words continuing the series), otherwise the computation and the reading would diverge on a
  video with an intro.
- **The push-in plan on highlights** in `plan["zoom"]`: `cam1.take.yellow` / `cam2.take.yellow` carry
  `strong` (the "Only strong highlights" checkbox), `min_pct`, `max_per_piece`, `second_min_s`, and
  `scores`/`uncomputed` — the word strengths from the sidecar. The `min_pct` threshold is a percentile of
  the strengths of the highlights OF THE CLIP (`layout._percentile`), a phrase is strong by the maximum of
  its words, and the cycles are counted by the edit, not by strength: the push-ins are in time order.
- Style: `font, hl_font, hl_bold, hl_fill, sub_fill, sub_case ('upper' | 'lower' |
  'sentence'), intro_font, intro_hl_font, transition,
  transition_sfx, pop, music_db, roto, roto_bottom, roto_device, roto_video,
  insert_style, intro_mode, cam1_zoom ('pulse' | 'jump' | 'drift' | 'none'),
  cam1_zoom_start (bool, default True), cam2_zoom ('pulse' | 'jump' | 'drift' | 'none', default 'none'),
  cam2_zoom_start (bool, default True), caption (bool), caption_font, caption_size,
  caption_fill, caption_x, caption_y, caption_bg (bool), caption_bg_fill,
  caption_bg_op, caption_bg_round, caption_kx, caption_ky, caption_case ('upper' | 'as-is')`
  (see `core/styles.py`, `resolve()` inherits from `base`; `None` for fonts = "as the base/as the
  subtitles").
  The highlight strength controls — `cam1_yellow_zoom_strong` / `cam2_yellow_zoom_strong` ("Only strong
  highlights"), `hl_zoom_strength` ("by emotion" / "by voice"), `hl_zoom_min_pct` (70 — p70 of the
  highlights of the clip), `hl_zoom_max_per_piece` (2) and `hl_zoom_second_min_s` (8); the group is shared
  by both cameras because the highlight words of a video are the same (see `docs/HIGHLIGHT_SPEC.md`).
  `caption` defines a single visual caption of the video with a configurable plate-background over the
  whole timeline (the text is taken from `<stem>.caption.json`). `sub_fill` — the color of the base
  subtitles `[r,g,b] 0..1` (default `[1,1,1]`, highlights take `hl_fill`), `sub_case` — the case of the
  subtitles in the AE assembly (in XML and Premiere the subtitles remain uppercase).
- Style keys (for all of them the default = the previous behavior, the `.jsx` does not change by a single
  byte — this is caught by the golden test; except for a deliberate change of the default assembly with an
  update of the reference and an entry in `CHANGELOG.md`): `sub_scale` (the scale of the subtitle precomp
  LAYER, %; the layout inside the precomp — line wrapping, auto-shrink, stack step — is not recomputed;
  at ≠100 the anchor/position of the layer are moved to the line point `[W/2, POSY]`, otherwise `sub_y`
  would start lying); `insert_anim` (`'zoom'` | `'rise'` | `'none'`; `none` works with ANY `insert_style`
  and removes `wiggle` — the jitter is damped by the `ins_wiggle` substitution in the template, not by a
  plan field); `insert_fx` (`'card'` | `'white'` | `'none'`; `none` removes both the shadow and the
  rounding mask, which is why the "Mask W/H" fields in the insert card are shown only with `card`);
  `insert_above_subs` (bool; photo inserts rise above the subtitles AND above the roto — the photo on top
  of everything, including the person; with the "Cam 1" style the fly-out from behind the back loses its
  meaning, this is a conscious choice).

- **The final voice of the clip `<stem>.voice.<key8>.wav`** (next to the XML, `core/voicefx.py`) — the
  baked processed voice of camera 1. `key8` — the first 8 characters of the processing cache key
  (`final_voice_key`), that is, the name changes together with the settings: a new track lies NEXT TO the
  old one, not on top of it. The version name and the key lie in the sidecar
  `<stem>.voice.json` — `{"key": …, "src": …, "file": "<file name>"}`; the sidecar is written atomically
  AFTER the file appears, so that a reader does not get a dead path.
  Readers take the path ONLY with the `final_voice_path` resolver: the name from the sidecar if the file is
  in place, otherwise the old `<stem>.voice.wav` (clips baked before versioned names are read as they were
  read). Why a versioned name: under a permanent track it would have to be REPLACED in place, and on
  Windows a replacement fails if the file is held open by the preview player (it plays exactly this file),
  by the `/api/media` serving or by an open AE/Premiere project — the new voice would not appear at all,
  and the failure would be silent. The preview player plays an UNCHANGEABLE copy in the cache
  (`cache_path`: a name by the content of the settings), while the version file next to the XML is read by
  AE (`final_voice_for_build`), Premiere XML (`core/xmlbuild.py`), `.drp` (`api/build.py`) and the draft
  (`core/draftrender.py`); other versions are removed best-effort — a busy one will be deleted next time.
  The count per clip is ONE: `ensure_final_voice` holds a lock by `realpath(xml)`, and a second order for
  the same clip gets the ready track without a computation. A baking failure at assembly is visible in its
  result: a clip with processing enabled and without a connected voice lands in `JOB["failed"]` with an
  entry carrying `warn: true` and a reason (`api/build.py`, `_voice_warn`), and the interface shows it as a
  warning — the assembly window and the end toast (`static/app/90-ae.js`), not a log line.

**The scene plan (`/api/scene`).** The same computation as the assembly, but WITHOUT roto and writing —
what the step-3 preview draws and from which the `.jsx` is then assembled
(the preview computes nothing on top, the one-source rule). A draft of the contract:
`{fps, w, h, dur, name, cams:[{ci, path, proxy, segs:[{ts,te,src}]}], zoom:{keys,
ease, holds, fit, pan, rot, follow}, intro:[{group, ts, te, on2, lines:[{words, times}], color, dx, dy,
ds}], intro_fsize, intro_cam, lumetri, inserts:[{ts, te, media, kind, style, oncam2, scale, pos, enter, exit, slack,
anim}], subs:[{s, e, w, color, row, gend}], roto:[{ts, te, ci}], caption:{text, font,
size, fill, x, y, case, bg, bg_fill, bg_op, bg_round, kx, ky}, audio:{voice_src,
voice_db, music_path, music_db, censor:[{ts,te}]}}`. The insert and intro keys arrive
ready (`anim`, `ts`/`te`) — the preview only interpolates between them by the pair of
influences `(in, out)` from the plan.

Plan fields (the preview only READS them and computes nothing on top):

- `zoom.holds` — a list of 0/1 by keys (replaced the single `zoom.hold`): on a HOLD segment the value of the
  left key, on a smooth one the previous curve (`ipvZoomAt` → `keysAt(…, holds)`);
- `zoom.pan` — `[pan_x, pan_y]` in px of the composition (frame shift, ZB); `zoom.rot` — the horizon in
  degrees; `zoom.fit` — the frame fill: after ZE it is always `100` here, because the `cam1_fit` multiplier
  is already multiplied into the zoom keys themselves in exactly one place (`scene_plan`);
  `zoom.follow` — `{keys: [[frame, px],…], ease}` of head following; with the checkbox off there is no key
  at all, so that the plan does not change;
- `lumetri` — `None` with the checkbox off, otherwise nine Lumetri values (the exposure of the clip is
  already added, ZJ);
- `intro_fsize` — the intro font size BEFORE the auto-shrink of lines, `intro_cam` — whether the intro
  rides with the Camera 1 null.

**Escaping:** any string into JS goes through `_js()` — it escapes `\ " \r \n \t`.
Windows paths → double backslashes. Multiline text (disclaimer) — `_js_multiline` (`\n`→`\r`).

## Gotchas (from real bugs)

- **Two doors to one state is a bug, even if both "work" (2026-10-02).**
  On step 3 an insert lives in two places: in the PREVIEW list and in the clip card (what is
  saved). Dragging and stretching on the timeline edited only the preview list, and on the
  next opening it was rebuilt from the cards — the timing edit silently disappeared.
  The same with the Camera 2 push-in (two copies of the rule "which keys") and with the glow
  of intro lines (the `fx` field in the line against the style checkbox `intro_accent_glow`).
  The rule: a value has ONE source; the second door must write where the first one lives (a
  common card lookup helper — as for the `x`/`y` shift), and a "saved but unreadable" field is
  better deleted. The check is not "the function was called" but "the value reached the state
  and survived a re-opening".
- **How many videos the card cuts is decided by free VRAM, not by desire (2026-10-07).**
  Every cutting process holds its OWN CUDA context of ~568 MiB until exit (measured on a
  4096 MiB card: four processes = 2307 MiB, peak under work 696 MiB), so the number of videos
  set in the settings (1..16) is clipped by free memory:
  `core/device.py:parallel_width_budget` = `free_vram // CUT_ROLE_VRAM_MIB` (600 MiB),
  measured as in `doctor.py` (torch, then `nvidia-smi`); on a machine without CUDA there is no
  ceiling, and the reason goes as a line into the job log ("card 4.0 GiB, each video holds
  ~600 MiB — no more than 6 at once (10 were requested)"). Before, ten videos on 4 GB gave an
  `OutOfMemoryError` in the subprocess and "code 1" without a reason. Same place: **subtitles
  (`POST /api/gen_subs`) recognize under `gpu_lock("субтитры")`** — without the lock, two ASR
  models (Whisper and GigaAM) ended up on top of a running cut on the card, — and a model that
  did not fit is retried in `int8_float16`, then one stage lower
  (large-v3 → medium → small) with the reason in the log; when nothing fit, the answer carries
  the code `whisper_gpu_fallback` with a translation. Memory exhaustion is recognized by the
  type or the text of the exception in one function — `core/device.py:is_oom_error`. The guard
  is `tests/test_gpu_budget.py`.
- **The shadow in the preview is a translation of Drop Shadow into CSS, not a "similar number"
  (2026-10-07).** The preview drew shadows with its own hardcoded numbers (`soft/2` for the
  intro precomp, its own `text-shadow` for subtitles, `drop-shadow(0 6px 18px …)` for inserts,
  nothing for the plate), and the shadow came out 1.34 times wider than the one assembled in AE.
  A measurement by an AE render in 2026 (a white square in a precomp, a black shadow,
  Opacity 255, Distance 0; Softness 50/150/287 → Gaussian sigma 9.5/28.0/53.5 px by fitting an
  erf edge profile, rmse < 0.004) gave σ = 0.187·Softness; CSS blurs `drop-shadow` with σ = r/2,
  hence `r = 0.374·Softness`. The translation is ONE door for the whole preview,
  `aeShadowCss(sh, k)` (`static/app/85-inserts-view.js`): `k` — preview pixels per frame pixel,
  the offset `dx = Dist·cos(Dir)`, `dy = Dist·sin(Dir)` (Y in the frame points down),
  `alpha = Opacity/255` one to one. It is called by the subtitles (a filter on the word
  container — this is the Drop Shadow of the precomp layer, so it covers the word background
  too), the subtitle background, photo inserts (the filter is composed with the entry blur in
  the AE order) and the intro precomp. The shadow numbers of the subtitles, inserts and the
  plate live in one place of Python (`core/xml2ae/plan_decor.py:shadows_plan` — the constants
  `SH_SUB_*`, `INS_SH_*`, `SUB_BG_SH_*` from `core/xml2ae/layout.py`) and travel to the preview
  as `plan["shadows"]` (`sub`, `ins`, `sub_bg`; `op255` — the AE 0..255 scale, the percentages
  of the control are converted by multiplying by 255/100), and into the `.jsx` — by the same
  substitutions; the preview has no shadow numbers of its own. With the style defaults the
  assembled `.jsx` is byte-for-byte the previous one. The bench is `tests/test_shadow_css.py`.
- **GigaAM-Emo accepts a PATH, and an array only through `forward` (2026-10-03).**
  `get_probs` on the `emo` head does `prepare_wav` → `load_audio` → ffmpeg, that is, it expects
  a string: a call with an already read array fails with
  `TypeError: expected str, bytes or os.PathLike object, not ndarray` (caught in a live run).
  Running the window through a temporary file is not allowed either — the window is its own for
  EVERY highlight word (dozens). Therefore `core/emphasis.py:emotion_probs` repeats the steps of
  `get_probs` over an array: mono float32 → 16 kHz (`EMO_SR`) → a tensor on `_device`/`_dtype`
  of the model → `forward` → `avg_pool1d` over time → `head` → softmax → `id2name`. The error
  is visible only in a live run: a model stub in a test does not reproduce it.
- **AE merges zoom keys on one frame — and the HOLD/ease flags shift (2026-10-02).**
  A jump on a cut and the start of a highlight push-in on the same frame gave TWO keys on one
  frame; AE merges them and moves the ease/HOLD flags to the neighboring key — the push-in
  became a jump ("it breaks off"), and camera 2 opened the edge of the frame up to 169 px. Such
  keys are merged into one in the plan, in the `.jsx`, in the preview and in the follow — by ONE
  rule. Nearby: the shift clamp must compute the scale by the AE CURVE and take the SMALLEST
  scale on the segment (by a straight line it missed), and the follow samples must be placed on
  EVERY sample (10/s) linearly and in a batch: thinning with a 4 px tolerance and Easy Ease on
  every key took the line past the edge and made the camera "brake" at the keys. This can only
  be measured in AE (frame by frame); Python geometry tests do not see it.
- **Interface geometry tests run in Chrome, not by eye (2026-10-02).** The window layout of
  "Inserts" and of the step-3 preview is checked in a real browser: one window height with 2 and
  with 30 inserts, no page scrolling, the timeline inside the window, and the step-3 layout is
  compared with a CSS bench with a tolerance of ±2 px. Why: frame errors (the height grew with
  the list, a hidden card column stuck out into the step-3 preview) are invisible in jsdom at
  all — a JS bench without a layout engine misses such defects.
- **JSX tests must mock the asset resolver (2026-08-28):** `core/xml2ae/build.py` resolves
  transitions and SFX via `assets.resolver()`, which returns an empty string when a file is
  missing (disabling the layer). JSX tests that expect a media-dependent layer (for example,
  transitions in the layer stack) must set/mock the resolver themselves with pytest tools
  (`monkeypatch`): the personal `assets` are not a fixture, and without isolation the test
  accidentally passed locally thanks to the developer's files, but failed on clean CI.
- **A precomp layer and a shape layer scale differently (2026-08-25).**
  The AE formula: `screen = Position + (P_local − Anchor) * Scale`. For a PRECOMP layer the local
  coordinates coincide with the pixels of the composition, so the anchor `[W/2, POSY]` places the
  scaling center exactly at the subtitle line. For a SHAPE layer (the subtitle plate, `bgLayer`)
  the content is drawn around the local `(0,0)`, and the same anchor takes the plate to
  `(162, 343)` — the top-left corner of the frame. For a shape layer the anchor is computed as
  `[0, POSY − bg_y]` (`bg_y` — the original `Position.y` of the plate), and the position —
  `[W/2, POSY]`. What must be checked is the RESULT by the screen formula (where the center ends
  up), not the fact that `setValue` was called: a test that checked the call missed the defect.
  Same place: the preview and AE must match — the plate in the preview lies INSIDE `#ipvsub` and
  moves by a common `transform: scale()`, so a wrong anchor in the `.jsx` gave the difference
  "one thing in the app, another in AE".
- **A provider keepalive defuses a timeout (2026-08-10).** Symptom: batch intro markup "hangs on
  the third file", the last log line is "… 16s · writing answer: 505 char", and the call is in
  its twelfth minute. The OpenRouter upstream fell silent in the middle of an answer, but
  OpenRouter itself kept sending `: OPENROUTER PROCESSING`. The only safeguard was
  `urlopen(timeout=600)`, and it measures silence **in the socket** — every keepalive zeroed it,
  and the call would never have failed. Worse: the progress tick stood AFTER the `continue` for
  non-`data` lines, so the "the model is alive" indicator froze exactly when it was needed. It is
  cured by a watchdog on the GROWTH of the answer (`STALL_MID`/`STALL_FIRST` in
  `core/aicut/llm.py`), not on bytes. The general rule: **for a stream, the transport timeout is
  not the task timeout.**
- **The name `t` in `static/app/` is taken by the translation function (2026-08-10).** The i18n
  pass wrapped strings in `t('…')` inside `ipvUI(t)`/`ipvOverlay(t)`, where `t` is the playhead
  time: `t('ФОТО')` called a number. It failed silently and in unexpected places — the playhead
  did not drag, the slider did not work, of two overlapping inserts only one was drawn, the
  generate button did not reach `fetch`. Time is now always `tm`, an element is `el`; the ban on
  the name itself is guarded by `test_nothing_shadows_the_translator_t`.
- **The source timecode: `probe()` asked only `v:0` for it (2026-08-06).** Symptom: the XML opens
  in Premiere perfectly, but in DaVinci Resolve the cut is laid out in the wrong place — "pauses
  are not cut, it is not that at all". Sony cameras (XAVC) put the timecode into the service
  track `tmcd`/`rtmd`, not into video stream tags, so `ffprobe -select_streams v:0` found nothing
  and zeros went into the XML. Premiere does not notice this — it takes the media by `pathurl`
  and relies on `pproTicks`; **Resolve positions clips BY TIMECODE**, and from the zeros the
  whole cut shifted by hours (`C1412.MP4` starts at `01;57;31;11`).
  The timecode must be read from ANY stream or from `format_tags` — `xmlbuild.pick_timecode()`.
  **Semicolon + `DF` is correct, do not "fix" it:** compared with Premiere's own export of the
  same timeline, there it is `01;57;31;11` with `ffprobe` `01:57:31:11`.
  The method that should have been applied right away instead of guessing at hypotheses: **open
  our XML in Premiere and re-export it into FCP7 XML** — you get a reference in the Premiere
  dialect for the same timeline, then an ordinary diff. It showed that all 299 clips
  (`start/end/in/out/pproTicks`) and all `<file>` declarations match BYTE FOR BYTE, and the only
  difference in the whole document is the timecode. The same diff removed false versions:
  `in/out` in 60 fps frames with `<file>` 29.97 is exactly the Premiere dialect and Resolve
  understands it; `file://localhost/C%3a/…` with lowercase hex and Cyrillic is also like
  Premiere, no need to change.
  Old cuts are fixed on the fly on download: `xmlbuild.fix_timecodes()` + `/api/export_xml` (we
  do not touch the file on disk — the editor, the subtitles and `xml2ae` read it).
  Two Resolve differences remain that are **not bugs** and are the same for an export from
  Premiere: the subtitle graphics (`GraphicAndType`) is always offline there (it has no
  `<pathurl>`; the `.srt` lies next to it), and `Basic Motion scale` requires Project Settings →
  Image Scaling → Mismatched Resolution Files → "Center crop with no resizing", otherwise
  Resolve first fits the 4K into the frame itself and applies our 50.4% on top.

- **The preview plays camera PROXIES, not the sources (2026-08-06).** The symptom was: "at a
  joint it stops for a third of a second", and only on new material — old clips with dense
  cutting flew. The cause is not in the code: H.264 **High 4:2:2, 10 bit** (Sony/Canon; the
  `камера1 джаггер` files — 3840x2160 rotation=90) is NOT taken by the browser to the hardware
  decoder. It is checked with one line, no guessing needed:
  `navigator.mediaCapabilities.decodingInfo({type:'file',video:{contentType:'video/mp4; codecs="avc1.7A0033"',…}})`
  → `powerEfficient:false` (for 4:2:0 8 bit `avc1.640033` — `true`). A software decode of 4K
  gives a seek of 365 ms median / 640 ms maximum; the same material at 720p 4:2:0 8 bit —
  124/375 ms. It is cured by `draftrender.build_preview_proxy` + `/api/preview_proxy`: the proxy
  is assembled **from the SOURCE**, once per camera file (26 s and 28 MB instead of 2.38 GB),
  cached in `<outdir>/_tmp/pv_*.mp4` by mtime/size. Cut edits do NOT touch it — this is how it
  fundamentally differs from the draft (`render_draft`), which must be rebuilt after every edit.
  Three differences from the draft proxy are mandatory: **with sound** (camera 1 is the sound
  source of the preview), **the fps of the source** (the editor playhead walks this same
  `<video>`), **guaranteed yuv420p 8 bit**.
  The fourth difference (2026-08-07): **a short GOP — a keyframe about once a second**
  (`-g <fps>`, not the default 250 frames = 10 sec at 25 fps). Otherwise every browser seek
  (scrubbing in the editor, the stand-in running up before a joint) drives the decoder from the
  nearest keyframe — up to 10 seconds of video forward — and the preview stutters even on ready
  proxies. A format mark `:g` (`preview_path`) was added to the cache key so that pre-fix proxies
  are guaranteed to be rebuilt.
- **The proxy size is computed by the DISPLAY frame, not by the encoded one.** A vertical often
  lies as 3840x2160 with a 90° rotation matrix. By the encoded one it would be 1280x720 — a
  vertical smeared into a horizontal. See `draftrender._display_dims` (the result is cached by
  mtime/size: the proxy plan is computed on every opening of the preview and on every progress
  poll of the assembly).
- **A rotated source is NOT run through NVDEC AT ALL** — `draftrender._decode_tries`, one list of
  attempts for both proxies (draft and preview). With `-hwaccel_output_format cuda` the ffmpeg
  auto-rotation is not applied: `scale_cuda` receives a not yet unrolled 3840x2160 frame,
  squeezes the landscape into a portrait, and the 90° rotation matrix goes into the output as
  is — the proxy comes out both distorted and lying on its side. The cunning part is that the
  attempt **succeeds** (NVDEC works on 4:2:0), so a broken file got into the cache, while on the
  CPU attempt the same source gave a correct frame: what settled in `_tmp` depended on which
  attempt worked. Measured on `камера1/C1387-008.MP4` (3840x2160, rotation=90, 4:2:0): the GPU
  attempt gave 720x1280 **with rotation=90 preserved** (that is, 1280x720 was shown, distorted),
  the CPU attempt — 720x1280 without rotation, correct. The CPU is only DECODING, encoding stays
  on NVENC. For rotated ones `|rot` is added to the cache key (`_rot_key`), otherwise a pre-fix
  broken proxy would be reused silently; for non-rotated ones the key does not change and they
  are not rebuilt for nothing.
- **A joint in the player is passed by swapping the `<video>`, not by a seek.** `currentTime=` at
  the moment of a splice is an honest decoder seek (flush → keyframe → decode forward), and the
  sound lands together with the picture (it is from the same element). Next to the cameras lives
  a **stand-in** (`P.spare`, OUTSIDE `P.vids` — otherwise `camVisual` would show it as a separate
  camera): it is taken in advance to the run-up `PV_PREROLL` before the needed frame, it is
  started live and mute before the joint, and at the joint the elements swap places. The live one
  is ALWAYS `P.vids[0]`, so the editor, the sound and `camVisual` know nothing about the swap. If
  it did not make it — a rollback to the previous seek, no worse than before.
  The machine (`bufMake/bufDrop/bufIdle/bufArm/bufRoll/bufTake` + the plumbing
  `sparePrime/spareRollAt/spareSwap/spareIdle/spareStop`) is **shared by all players**:
  connected are the edit `PV`, the editor `ED`, the inserts `IPV`, the camera layout `CPV`. This
  holds on the fact that the target is an absolute source time (`b.at`), not an index in
  someone's segment list; that is why the editor uses the stand-in of the edit player without
  starting its own.
  **A player can have several stand-ins — `P.bufs`, one per CONTINUOUS track.**
  All of them have slot 0 (the leading camera, which is also the time base). In `CPV`, when the
  sound is from K2/K3, a second one is added for the sound camera: without it,
  `cpvSyncAudioCam` jerked it on every splice (tolerance 0.25 s), that is, the sound landed
  exactly where the picture used to land. This stand-in has its own file (recreated when
  K1/K2/K3 changes, `cpvAudioBuf`) and its own constant offset `b.off` = `CPV.delta[k]`; after a
  successful swap the discrepancy is zero and the synchronizer is silent by itself.
- **A secondary camera is a SECOND CONTINUOUS TRACK, not a sleeping frame (`camApply`,
  2026-08-08, third attempt).** This is that very complaint "in the preview with 2 cameras,
  when switching there is blinking, not an exact switch", and the first two attempts treated the
  symptom: they kept hidden cameras paused and woke them at a joint (the decoder was late → extra
  frames of the FORMER camera on screen — "the previous one flickers") or kept them playing but
  corrected drift with a seek (the seek itself drops a frame and gives the same rollback of the
  camera angle).
  The correct model: a clip is shot by one take, the sync is one for the whole video, so the
  source time of camera k = the source time of the leading one + a **constant offset** `P.delta[k]`
  (`camDeltas`, the same computation that used to exist only for the sound camera in the layout).
  Hence: a camera plays ALWAYS; the drift is damped by SPEED (`camTrack`, ±6 % at a discrepancy
  >0.04 s; a seek — only from 0.5 s, that is, on scrubbing and on a swap failure); it passes a
  joint with ITS OWN stand-in (`camBufs` starts one for every camera — a generalization of what
  the sound one had). Showing a camera angle becomes a pure z-index change: the frame is already
  the right one.
  **The previous angle is NEVER shown** — `camApply` takes `ci=s.ci` as is.
  Any "let's wait for the incoming one to be ready" is showing ANOTHER camera, and the loop runs
  60 times a second: "hold it for a couple of frames" is exactly a visible flash of a foreign
  angle. That is how it was: first up to 500 ms, then up to 100 ms — the complaint did not go
  away. The worst that is allowed now is a couple of frames of the SAME angle, still finishing
  its seek. One machine for all players: there were three copies, the edits diverged.
- **THE MAIN THING about blinking: the CLOCK jitters, not the display.** The edit time is
  computed from `currentTime` of the LEADING camera, and at a joint it is replaced by the
  stand-in, which is allowed to stand in the window `PV_SWAP_LO..HI` (up to 0.12 s short of the
  target). Right after the swap `t` is computed from it — and for a frame or two it ROLLS BACK
  beyond the piece boundary. `pvSegAt` honestly returns the previous piece, and in it the former
  camera: the display jumped "new → previous → new". Three fixes of the display logic survived
  this, because the display worked correctly — the input was lying.
  It is caught by a counter, not by eye: on ONE camera angle change "кам" gained 4 instead of 1
  (the user's measurement on C1432). Reproduction with synthetic data — the sequence of t
  `−0.30, −0.10, +0.02, −0.06, +0.04, −0.05, +0.10, +0.30` from the piece boundary gave the
  display `0,0,1,0,1,0,1,1` (5 changes), with backward movement forbidden —
  `0,0,1,1,1,1,1,1` (1 change).
  The cure (`camApply`): during playback by EDL pieces we do not go back. Back is scrubbing, and
  it always goes through `*SeekTo`, where `P.vidx` is reset to −1 and the ban is lifted.
  In `#ipvstat` there is a "rollback" counter for this: how many times the clock tried to go back.
- **The angle is switched by LAYER ORDER, not by opacity (`camVisual`).** A `<video>` layer with
  `opacity:0` may not be drawn by the compositor at all, and on return to `1` the first frame
  arrives late — at that moment what was under it remains on screen, that is, the FORMER camera.
  This is not visible in the player logs at all (video frames have nothing to do with it), it is
  caught only by eye — and this is the reason the "blinking" survived two fixes of the logic. Now
  all cameras are opaque and always drawn, only `z-index` changes:
  the visible one — 2, the others — 1, the stand-ins — 0 (they play ahead and must not surface).
  It holds on the fact that a camera covers the frame entirely (`.pvstage video`: `inset:0` +
  `object-fit:cover` + a black background) — guarded by a test.
  The display honesty counters are visible right in the preview (`#ipvstat`):
  "joint · swap · seek" — about the splice, "кам · замер" — camera angle changes and frames on
  which the incoming camera was still finishing its seek. Measured on step 2 (23 segments, proxy):
  385 display calls, 13 switches, **0 displays of a foreign angle**, 13 joints out of 13 by swap,
  "замер" 2 frames.
- **The camera frame in the AE preview is drawn by `<canvas>`, not by a CSS scale of the `<video>`
  (`ipvCamPaint`, 2026-09-08).** `transform: scale()` on a `<video>` with a smoothly changing zoom
  gave a horizontal jitter of the frame, the more noticeable the larger the push-in. Measurements
  proved the data has nothing to do with it: the edit time is monotone (0 rollbacks on 375
  samples), the zoom curve is monotone (0 reversals on 309 samples), frame drops 0, the proxy adds
  no jitter (inter-frame shift 0.0156 px against 0.0165 px for the source). A bench of three
  output methods (CSS-scale on video / canvas / wrapper scale) chose canvas.
  Now the `<video>` elements remain SOURCES (sound, stand-ins, `camVisual` with its `z-index`) and
  are hidden with `visibility:hidden` — NOT `display:none`: otherwise the browser stops decoding
  frames. Above them is `#ipvcam` (`z-index:3` — below the roto mask 4 and the subtitles 5,
  `pointer-events:none`, otherwise choosing the push-in point by clicking the frame breaks).
  The frame model is ONE matrix for all cases (`ipvCamMatrix`):
  `screen = C + S·(R·p − C_c) + T`, where `C` is the push-in point, `S = ipvZoomAt(tm)` (the fill
  is already inside the keys), `R` is the rotation of `plan.zoom.rot` around the source center,
  `T = ipvCamShift(tm)` (`pan` from ZB plus the follow from ZC). The WHOLE source is drawn
  (`c.setTransform(matrix)` + `drawImage(v, 0, 0, vw, vh, …)`), not a crop of the screen frame:
  the previous crop (`f = max(W/vw, H/vh) · s`, `sw = W/f`, `sh = H/f`, `sx = cx·(vw−sw)`,
  `sy = cy·(vh−sh)`) with a shift and a horizon opened edges that do not exist in AE, while
  `drawImage` with a source rectangle beyond the video gave a smear on the left. The push-in point
  stays fixed (137.35 px both at `s=1` and at `s=1.35`).
  Two consequences that have already bitten: it must be drawn also at `s === 1` (the early exit
  was removed), and on the `loadeddata`/`seeked` events of EVERY element, including the stand-ins
  — otherwise after scrubbing the canvas is empty, while before the `<video>` showed itself. The
  zoom value is quantized by composition frames inside `ipvZoomAt` — one value per frame, as in AE.
- **`ipvCamChild` is only for children of the Camera 1 null.** It adds the zoom, the `pan` shift
  and the follow to the position. Inserts of cam2 and "cam1 on cam2" sit on FREE nulls — it is not
  meant for them, otherwise a foreign insert would move together with the Camera 1 frame. One
  matrix `ipvCamMatrix` describes both the frame and the hint at the bottom of the roto mask (its
  CSS transform goes from `transform-origin: 0 0`), while `ipvLumetriFilter` builds a hidden
  `<svg>` filter from `plan.lumetri`: `feComponentTransfer` (tone curve) → `feColorMatrix`
  (temperature/tint balance) → `saturate`. The Lumetri formulas are closed, this is an
  approximation; the filter is rebuilt only when its own set changes and is set on the `drawImage`
  of its own camera. Camera 2 has its own set only with an OPEN link chain (`lm2_link=False`):
  then the plan carries `plan.lumetri2`, and its frame takes a separate filter `ipvLumetri2`; with
  a closed chain there is no `lumetri2` key in the plan, and Camera 2 takes the Camera 1 filter.
- **The font in AE was reset to Times because of duplicate PostScript names
  (`_fontPick`/`setFont`).** AE sometimes has TWO entries with the same PostScript name (a trace
  of a font reinstall): by name it takes the broken one, and `try{d.font=…}catch(e){}` swallows it
  silently — the text stays in the default font. Now ALL font settings in the `.jsx` go through a
  single `setFont(d, ps)`: it takes a working copy from the `_FONT_PICK` cache
  (`app.fonts.getFontsByPostScriptName`, tested with a temporary text layer) or sets it by name;
  if no copy was set — `_LOG` with the font name and `null`. Outside these two functions there
  must be no `.font =` assignments in the assembly.
- **The stand-in catches up with the joint by SPEED (`bufRoll`).** The tick on which we decide to
  start it itself arrives late (in a background tab rAF is silent and a safety interval of 120 ms
  remains), and the stand-in came up ~0.15 s short — beyond the `PV_SWAP_LO` window. The swap
  failed on a third of the joints, the joint went through a seek, and on the seek the incoming
  camera was not ready — that is where the extra frames of the previous angle came from. It is
  mute and invisible, so the shortfall is damped by speed: "how much media is left to pass" /
  "how much time is left" (clamps 0.25–2.5), and `bufTake` returns `playbackRate=1` on going live.
  Measured on a real 2-camera clip (23 segments, proxy), in DELIBERATELY worse conditions — a
  background tab, only the 120 ms tick: 14 joints out of 14 pass by swap (was 10 of 13), seek
  fallbacks 0, switch delays 0 (was 3 events up to 0.06 s), the angle discrepancy with the leading
  one ≤0.05 s, the catch-up speed does not leak into the live output. In the camera layout (CPV) —
  12 switches, delays 0.
- **Cleaning `_tmp` does NOT touch proxies unless explicitly asked.** `clean_tmp` is called
  automatically before EVERY cut in the result folder (`api._run_cut_job`): if it wiped the
  proxies, every new cut would reset the preview for all clips of the folder at once. A proxy is a
  cache by camera file, it does not depend on the edit and must survive cutting. The 🧹 button
  asks about them separately, having shown the size (`/api/tmp_info` → `proxy_mb`/`other_mb`), and
  passes `proxies=true`. The cache key (`preview_path`: abspath+mtime+size+height) is stable —
  verified by an audit: 5 proxies for 5 sources, zero extra.
- **The stand-in run-up is NOT prepared while dragging the slider.** `oninput` fires on every
  pixel, and `pvScrub` calls `pvPause+pvSeekTo+pvPlay`. A stand-in seek on every event left it
  forever `seeking`, the swap at the nearest joint failed — the symptom is exactly "I moved the
  slider and it stalls again", with smooth normal playback.
  The guard is `PV.scrubbing` in `pvSpareArm` (shared, it covers the editor too).

- **The AE panel writes into the job ONLY of the clip loaded into it (`AEXML`, 2026-08-08).**
  `captureAE()` merges the panel (`INS`/`HL`/`BRK`/`INTRO` + style fields) into
  `CLIPS[curAE].job`, but the panel is filled by `selectAE`, while `captureAE` is also called from
  the side: `onStyleChange` — when styles load at page START and when a speaker is selected on
  step 1. At the same time `curAE` survives F5, while the panel is empty after a reload. The
  result: a clip opened last time on the AE step silently lost AI inserts, highlights and the
  intro — on an ordinary F5, without a single click. The user's symptom: "I made AI inserts, came
  back — and they are gone". It is caught via `ui_state.json`: `c.inserts=13`, while `job.ins=0`,
  `highlights=0`, `introRows=0` exactly for the clip under `curAE`.
  Now `selectAE` sets `AEXML=c.xml`, and `captureAE` on a mismatch only saves the state and exits;
  `openAEFor` compares not only the index but also `AEXML`.
  What is recoverable: `job.ins` is rebuilt from the step-2 markup (`ensureJobs`), the highlights
  are re-read from the XML (`loadWordsFor`). **Intro lines are not recoverable** — they live only
  in the UI state and will have to be marked up again.

- **The image generation prompt is assembled in `aicut.build_image_prompt`**, the settings — in
  the speaker profile (`speakers/*.json → image_prompts`). **There are TWO appendixes
  (2026-08-04):** slots `a` and `b`.
  On the insert card each slot has its own button ("1" and "2"), `/api/ai_genimage` accepts `slot`
  and `speaker`. The meaning: for the same subject you sometimes need a shot with text on the
  image, sometimes without. Batch generation ("generate the missing ones") goes with slot `a`.
  `extra` — the user's style appendix with a side choice (front/back). The appendix is applied ONLY
  to generation — it does not get into the `query` of the insert, otherwise the same text would go
  into the stock and library search and narrow the output. Empty or no profile — a clean subject is
  generated without a fallback. **2026-07-22 (user request):** the "technical tail"
  (`tech`, "single object, isolated on plain white background…") was **removed entirely**
  (`IMAGE_TECH_DEFAULT = ""`, the field was removed from the UI), and the appendix is glued with a
  **space, not a comma**: "broken eyeglasses 3d icon" is one nominal group, whereas "broken
  eyeglasses, 3d icon" is read by the model as TWO subjects and it draws a composition of two
  objects. If after this rembg starts smearing on a complex background, the tail can be returned
  with one line in `build_image_prompt`.
- **Images at OpenRouter go only through the dedicated Image API** (`POST
  https://openrouter.ai/api/v1/images`, `{model, prompt}` →
  `data[0].b64_json` + `usage.cost` in $). Through `/chat/completions`
  with `modalities:["image","text"]` purely image models (FLUX) answer **404 "No
  endpoints found that support the requested output modalities"** — the Flux profiles in the
  config were non-working exactly because of this. The catalog there is its own:
  `https://openrouter.ai/api/v1/images/models` (43 models), they do not get into the common
  `https://openrouter.ai/api/v1/models`. The parameters beyond `model+prompt` are different for
  every model (`supported_parameters`): for FLUX — `output_format/n/seed/
  input_references`, for gpt-image — `background/quality/resolution`; the extra ones are silently
  ignored, but only the declared ones should be sent. A transparent background is natively
  **supported by none of the FLUX models** (`background` exists only on 8 models) — rembg is
  still needed. Measurement: Klein ~5 s/$0.014, Pro ~15 s/$0.030, Nano Banana ~3 s/$0.034.
  Since 2026-08-26 the path is chosen NOT by provider name: the model is in the fetched image
  catalog (`aicut.images.IMAGE_MODELS`) — we go to the Image API; there is no catalog and
  `/images` answered 404 — we fall back to the chat path. Thus any provider with its own Image API
  works with FLUX, and a provider without one — as before, through chat.

- **`reasoning: off` != "do not send the parameter".** Models that think BY DEFAULT
  (`deepseek-v4-flash`, `tencent/hy3`, …) without an explicit `{"enabled": false}` go into
  reasoning for the whole `max_tokens`. Measured on a 224-word video (July 2026): highlights
  **126 s** / out=12001 (truncated by max_tokens → broken JSON → a repeat of the whole call)
  against **3.3 s** / out=192 with an explicit ban; inserts 86 → 18 s, intro 67 → 4 s.
  The mode turned on non-deterministically — hence "sometimes fast, sometimes a minute". From the
  same place comes the rule: **`max_tokens` is a safeguard, not a reserve**. It was raised
  4096 → 12000/24000 "so that the JSON is not cut" — and gave a stuck model three times longer to
  think. The real answers: highlights ~200 tokens, intro ~300, inserts ~1600.
  For reference: `reasoning: {"effort": "low"}` on the same request — **178 s** / out=11766.

- **We do NOT send `max_tokens` to OpenAI-compatible providers at all** (2026-07-22).
  It does not limit the model: it computes the answer in full, the provider bills it, and only OUR
  copy is cut off — that is, we pay and throw it away. Measured on C1355 (deepseek-v4-flash,
  medium): 9445 tokens, of which 8122 for reasoning, the answer truncated by our ceiling; a repeat
  with `max_tokens × 1.5` (11276 -> 16914) gave a third request, which the provider rejected — and
  we showed "API key not accepted (403)", because the provider's error text was thrown away.
  Without a ceiling the same clip passes in ONE request in 184s, the markup is intact.
  The parameter remained only on the Anthropic path (there it is mandatory by the API).

  The limiters are now honest and visible: a **timer** in the log distinguishes three
  states ("waiting for the first chunk" / "thinking: N char of reasoning, no answer yet"
  / "writing an answer: N char") and a **"Stop"** that tears the connection at the nearest
  stream chunk. The code does NOT lower the level of "smarts" itself — the user sets it, and he
  also sees from the timer how long the model thinks and decides. The contract is fixed by tests:
  `tests/test_ai_call.py` (there is no max_tokens in the payload, the level goes out as set,
  "Stop" fails the stream, a 5xx in the body of a 200-stream is a reason to retry).

- **The level of "smarts" is PER STEP, not one per profile** (2026-07-22).
  The steps need different things: cutting MUST think (measured on C1353, markup mode: without
  reasoning the model systematically bracketed the LATE take of a duplicate instead of the early
  one and carried away unique continuation with it — "в организме резко взлетает",
  "без защиты ты облысеешь"; with medium all brackets landed correctly), while highlights/inserts/
  intro must NOT (there reasoning burned the whole max_tokens for nothing).
  It is stored in `ai_config.reasoning_steps`, the defaults — `aicut.STEP_REASONING_DEFAULT`
  (cut=medium, the rest off), the level is passed to `_ask_json(..., reasoning=)` and overrides
  the profile one. `max_tokens` grows together with the level
  (`aicut.reason_budget`: +4k/+8k/+16k) — without a reserve the model spends the limit on thoughts
  and returns an empty answer. In the UI the "Smarts" control sits next to the AI profile choice:
  "Cut smarts" on page 1, "Smarts: highlights/inserts/intro" in the header of page 2; it was
  REMOVED from the profile settings (the gear). The field `profile.reasoning` remained in the
  config for backward compatibility and is used only by those calls that do not pass a level (for
  example "Check" in the settings).

- **The same in LM Studio, but a DIFFERENT parameter.** The OpenRouter `reasoning` was not sent to
  a local profile at all, while Qwen3.6-27b thinks by default: the C1353 cutting step — out=3000,
  answer 0 characters, "broken JSON after 2 attempts", that is, it NEVER reached the end. A direct
  LM Studio test: `chat_template_kwargs: {"enable_thinking": false}` is IGNORED by this build
  (reasoning_tokens 60), while the OpenAI-compatible `reasoning_effort: "none"` — works
  (reasoning_tokens 0). We send it for `provider=lmstudio` (`use_effort` in `_ask_openai`), with a
  fallback on 400. The step became 15-35 s and started returning JSON.

- **All AI settings are in ⚙, by tabs.** The `mbAISettings` modal has exactly six
  tabs in the order **"Connections" → "Cut" → "Markup" → "Generation" → "Words" → "Tools"**.
  "Connections" saves profile CRUD and diagnostics, "Markup" holds the matrix of three tasks,
  "Generation" owns images and video, "Tools" — actions and export,
  "Words" — two native details with a shared name=aiswords. The old common maps to tools.
  Links from #pageVideo use `openAISettings('generation')`.
  The control ids and server-side saving do not change. When editing fillEngineSel remember
  that it assigns sel.onchange as a property and overwrites the inline handler.

- **OpenRouter free endpoints return 5xx IN THE BODY of a 200 stream.** `nemotron-3-ultra:free`
  on a run of four clips failed one step: `{"code": 502, "ResourceExhausted:
  Worker local total request limit reached"}`. Before, this was an instant
  `SystemExit` and the loss of the whole cut. Now `_read_stream` throws `UpstreamBusy`
  on code >= 500, and `_ask_openai` repeats the request with a pause of 2/4/6s (max_tokens is
  NOT inflated here — the answer is not broken, the upstream is simply busy). Busyness has **its
  own counter `aicut.BUSY_RETRIES = 3`** (2026-07-22): before, it shared the budget with
  `retries`, and that equals 1 (this is the budget for fixing broken JSON) — and "retrying (1/1)"
  immediately turned into a refusal, although it is cured only by waiting.

- **A raw newline inside a string breaks the `.jsx`.** A subtitle word can wrap in a
  Premiere title (`ДИГИДРО\nТ*СТОСТЕРОНА`) → an unclosed JS string → "Unable to execute
  script at line N". The protection is in two places: `_js()` escapes control characters; in
  `parse_full` the word is glued (`" ".join(word.replace("\r","").replace("\n","").split())`).
- **`ncams` is an upper bound, not a hard number.** Before, the first `ncams` tracks were strictly
  taken as cameras → on a 1-camera file assembled with a set-wide `ncams=2`, the track of
  **photo inserts** became "Camera 2", `addCam` loaded only the first photo → one static photo is
  visible and "cameras: 2". Now a track is taken as a camera only if it really looks like a
  camera. The "cameras: 1" sign in the alert on a 1-camera file = everything was recognized
  correctly.
- **Insert nulls:** `insNull1` ("cam1 inserts") is bound to the Camera 1 Null (identically,
  follows the zoom), `insNull2` ("cam2 inserts") is free (world coordinates). cam2-photo
  → `insNull2`, cam1-photo → `insNull1`.
  **Exception — cam1 style on a cutaway (2026-07-28):** the photo insert style can be forced to
  "Cam 1" for the whole video, and then the fly-out from behind the back goes to inserts that are
  shown over Camera 2. Such ones hang on a THIRD null `insNull1b` ("cam1 inserts on
  cam2") — free, in the center of the frame (the same local coordinates): they must not ride the
  Camera 1 zoom, its camera is not in frame, and the photo otherwise crawled and changed size by
  itself. The sign is computed by Python (`x["oncam2"]` by `_active_cam_at` already AFTER the
  start is pressed to the cut), in JSX it is the `oncam2` flag of the insert. The rest point of
  such ones is shifted by the common pair `INS_C1_ON2_X/Y` (style `insert_c1on2_x/y`, the field
  "Fly-out on a cutaway, X / Y" in the UI) — for all of them at once.
- **Intro precomps + cross-fade:** every group = its own comp; it appears by its first
  word (the 1st group is visible from 0), fades out from its last word (the last one holds the
  tail). The roto "sandwich" is computed by the window of EVERY precomp and laid above its text
  (`introRotoAnchors[ig]`). The intro fonts are separate (`intro_font`/`intro_hl_font`, empty = as
  the subtitles).
- **The "not closer than N seconds" safeguard is NOT a way to set the rhythm (2026-07-30).** In
  `cmd_intro` the accents of "text behind the back" were filtered out by the rule "not closer than
  6 s by the start of the previous one", and it silently (without a log line) threw away 40% of
  the markup: comparing the AI proposal (`<stem>.intro.json`) with the assembled `.jsx` on the
  C1387–C1395 set showed that the user places accents by hand with a median gap of 4.2 s, and
  every second neighbor is closer than 6 s. Running his own markup through the old filters left 79
  of 148 groups. Now `_place_mids` looks at the END of the previous group (`INTRO_MID_GAP` = 0.4 s)
  — exactly what the safeguard was for (two precomps must not hang at once), — and writes every
  drop into the log. The numbers and the other edits (yellow by default, a target number of
  accents as a number in the job, we highlight the final call to action) are in
  `docs/INTRO_SPEC.md`. Test: `tests/test_intro_mids.py`.
- **Camera 1 Null zoom:** the pulse push-in is placed on the **return cam2→cam1** (the frame of
  the cut where cam1 is visible again) + **always on frame 0**. `182%→100%` over 62 frames, the
  curve `cubic-bezier(0.35,0.01,0.10,0.99)` on "big→small" segments (via
  `HL_EASE_OUT=35`/`HL_EASE_IN=90`).
- **Roto is CONTINUOUS for the whole timeline (2026-07-12):** NO more layout by insert windows
  (there were distribution bugs there). `_span_roto_plan(cams, 0, dur, fps)` = the whole visible
  EDL; every piece = a copy of ITS OWN camera + a luma matte, bound to the null of ITS OWN camera
  (pixel-for-pixel, including the zoom). Layers bottom-up: cameras → inserts/intro text → roto →
  subtitles. Roto layers are shy + label 9, `hideShyLayers=true` (the timeline in AE is clean and
  unfolds with the Shy button). The cost: RVM over the whole timeline (longer on GPU). The style
  flag `roto_video` was abolished (covered).
- **"Mask bottom %" is a share of the SOURCE, not of the frame on screen (2026-08-14):**
  `roto_bottom` fills the bottom of the CAMERA frame with white (`drawbox` in `core/roto.py`), and
  that is correct: the table stands in the same place of the shoot no matter how much you push in.
  The hint in the preview was lying — the red bar stood at the bottom of the stand, although the
  roto layer in AE hangs on the Camera 1 null and moves with its push-in. At a zoom of 160% from
  the point `cy=0.244`, the bottom ~10% of the VISIBLE frame is painted white, not 35%. Now the
  hint frame (`.rotomask`) carries the same `transform` as the video (`ipvRotoMaskZoom`, the scale
  comes from `ipvZoom` — a second zoom interpolator must not be started), the bar is a nested
  `.rmband` (the percentages of `transform-origin` are counted from their own box; for a bar 35%
  high they would miss the push-in point).
- **Roto v2 — a streaming pipeline (2026-07-14):** v1 (a segment file libx264 → RVM fp32
  seq=2 → a 4K PNG sequence → libx264) gave ~2 fps: the GPU idled (10–20%), all the time was eaten
  by PNG on the CPU and a double 4K encode; a 6-minute video ≈ an hour. v2 in `core/roto.py`: one
  ffmpeg NVDEC decode (`-ss` BEFORE `-i`) → pipe → RVM fp16 seq=8 → pipe → ffmpeg NVENC; without
  intermediate files and PNG. The mask is at HALF resolution for 4K (a luma matte is enough),
  the factor in the `f`/`mf` field (see the ROTO contract). ~25 fps on 4K vertical (RTX 4070 Ti S)
  — ~10x. Vertical sources: rotation=±90 in the side data → `_probe` swaps w/h
  (ffmpeg auto-rotates at decode; without the swap the frame is squashed). The cache key of the
  masks includes the resolution divisor — old v1 masks are not reused. env: `REELSI_ROTO_FULLRES=1`
  (full mask resolution), `REELSI_ROTO_DEVICE=cuda|cpu`.
- **A draft through the card is proxies, not GPU filters (2026-07-21).** The `_nvenc_ok()` test in
  `core/roto.py` ran a 64x64 frame, and NVENC rejects such ("Frame Dimension less than the
  minimum supported value"): the test failed ALWAYS and roto silently encoded 4K into libx264 on
  the processor. The test frame is 256x256. In `draftrender` the codec was NVENC even before — the
  CPU was loaded by DECODING: `trim` stands after the decoder, so every draft ran the whole 4K
  source through it entirely, and the draft is rebuilt on EVERY self-check iteration and after
  edits. Moving the filters naively to the GPU (`-hwaccel cuda` + `scale_cuda`) does not help:
  `concat` does not accept cuda frames, `hwdownload` has to be put in every branch, and on 60
  segments this is SLOWER than the CPU (measured 5.0 s against 3.8 s). What works is different —
  720p proxies of the cameras (`_build_proxy`: NVDEC + `scale_cuda` + NVENC, cache
  `_tmp/proxy_<hash>.mp4`, key = path+mtime+size+geometry): the heavy decode once, then the
  assembly goes over 720p (3.8 s → 0.8 s with 60 segments; on real 4K the gap is bigger).
  Fallbacks at every step: NVDEC → CPU decode (4:2:2 10 bit Sony/Canon is not supported by NVDEC
  before Blackwell), the proxy did not assemble → we work over the source. To disable:
  `use_proxy=False` / `--no-proxy`.
- **Transitions are above roto and front video (2026-07-14):** the "Transition"/"Whoosh" layers are
  created in the insert loop, while the roto sandwich and the rise of front video
  (`moveToBeginning`) come LATER, so the transition flash burned BEHIND the character and the
  full-screen video insert ("the video clip is above the transitions, but under the roto"). The
  fix: `transLayers[]` collects the transitions, and after roto and frontVideos they are lifted to
  the top. The order top-down: disclaimer → subtitles → NULLS → transitions → front video → roto →
  intro/photo inserts → cameras.
- **ALL nulls go to the head of the comp (2026-08-11):** the lifting of nulls was written as a
  by-name list (cameras → "cam1 inserts" → "cam2 inserts" → "intro"), and every null created later
  was forgotten in it: "cam1 inserts on cam2" and "intro on cam2" remained buried between the
  camera clips, and the only way to get them was to scroll the timeline. Now after the explicit
  order comes a **sweep of any remaining null** of the composition (`layer.nullLayer`) — a new null
  lands in the head by itself. The order of nulls does not affect the picture: they are disabled
  and serve only as parents. In the comparison a null is identified by the `isNull` flag from the
  `tools/ae_inspect.jsx` dump, not by name, and "null" is its own tier in `ORDER_PAIRS`.
- **An insert cut off by a cut has no exit animation (2026-07-14):** the AI rounds timings to
  0.1 s, so the end of an insert could land 1 frame from a camera change — the strict check
  "the change is inside the window" did not catch this, and the photo played a full fly-out over
  the changed shot. The fix is in `_clip_end`: an end within `SNAP_TOL=0.12 s` of the change point
  (photo AND video) → the end is pressed to the cut, `noexit=true` (for video this also removes the
  exit transition+whoosh).
- **ExtendScript (ES3, an old AE engine):** long one-line arrays are fine; only control characters
  in strings break it. `setTrackMatte` — AE 23+, there is a fallback to `trackMatteType`. The
  style/font is set through `Text Document`, `fauxBold` for artificial bold.
- **The camera layout rule (`align.assign_cameras`, rewritten 2026-07-22).** One function for all
  paths (cutting, the cut editor, the layout window). Common for any number of cameras: **the first
  and the last piece are camera 1** (the video opens and closes with the main shot). Then the
  layout is DIFFERENT by the number of cameras — this is important, do not confuse the rules:
  - **2 cameras** (`_assign_two`) — strict alternation 1-2-1-2, no exceptions: there is nothing to
    choose from, any deviation reads as a rhythm failure. Parity: alternation from cam1 comes back
    to cam1 only with an ODD number of pieces; with an even one, in exactly one place the camera
    holds two pieces in a row (from index `p` the phase shifts, then alternation again, the end is
    again on cam1). The place of the double is the pair of the SHORTEST neighbors (a delay on short
    ones is not caught by the eye), the first piece is not taken into the pair (the beginning must
    open with a clean change), with equal pairs — closer to the end and preferably on cam1 (two
    short ones in a row from the SECOND angle — that is the very cam2-cam2 pattern from the audit).
    Big pieces are NOT singled out here.
  - **3+ cameras** (`_assign_many`) — the previous layout in full: a BIG piece
    (>= `big_chunk_sec` = 6s) goes to cam1 (the only case when a camera repeats in a row), a return
    to cam1 every `return_every` non-first pieces, the rest — to the least occupied angle (2..N are
    divided equally). There is nothing to alternate here: there are many angles, "strictly every
    other one" would kill both the big pieces and the balancing. Only a tail was added: if the last
    piece came out not on cam1, it is put on cam1, and the doubled penultimate one is taken to a
    free angle (if it is not big — a big one goes to cam1 by right).

  Tests: `tests/test_assign_cameras.py`. After a manual cut edit the layout is recomputed by itself
  — `editor_save` calls `assign_cameras` and resets the manual `assign` from project.json (the
  blocks are different, after all).
- **The camera layout does NOT touch the markup (2026-07-22).** `/api/cams_save` changes only whose
  picture is visible on a piece: the `keep` intervals, their lengths and the sound (always camera 1)
  — the same, so the words, timings and highlight indices do not shift by a frame. Before, the
  rebuild went without `sub_words`, and saving the layout wiped the subtitles and highlights (plus
  a scary `confirm`). Now we read them with `parse_full`/`auto_highlights` BEFORE assembly and write
  them back — as `swap_cam` has long been doing. Do not confuse this with `editor_save` (the cut
  editor): there `keep` really changes, and resetting the markup is honest.
- **After Effects does not read webp and avif (2026-07-22).** It has no import of such files at all
  — the .jsx is assembled, but no layer appears in the composition. In the file picker dialog they
  are now shown (there are plenty of them in "Downloads"), and `insertlib.to_ae_image()` re-encodes
  them into PNG next to the source at assembly (the alpha is preserved) — the call stands in
  `api._adopt_inserts`, that is, on all three assembly paths, and the substituted paths travel to
  the front end with the same `insmoved` mapping as the move of files into the library.
- **A CMYK JPEG takes down the WHOLE .jsx (2026-07-27).** The extension is ordinary, AE knows the
  format — but on `importFile` it issues "Unsupported video bit depth in source file" and aborts
  the script on the very first import: it is not one layer that disappears, the whole project fails
  to assemble (the `09_ng18.jsx` case — a photo from a stock in the insert library). You have to
  look INSIDE the file: `insertlib.to_ae_image()` is now called for every image, not by a list of
  extensions, and everything whose mode is not from `AE_OK_MODES` gets `<name>-rgb.png` next to it.
  `core/verify_jsx.py` catches the same thing before AE.
- **AV1 video takes down the WHOLE .jsx (2026-08-01).** The same disease as CMYK JPEG, only with
  video: the `.mp4` container is familiar, but the stream inside is not, and `importFile` aborts the
  script with the dialog "The source compression type is not supported" (the `AutoCut_all.jsx` case
  — `videoplayback (12).mp4`, downloaded from YouTube, AV1). The codec is visible only to
  `ffprobe`, so `insertlib.to_ae_video()` looks at the stream of every video and re-encodes
  everything from `AE_BAD_VCODEC` (av1/vp8/vp9/theora) next to it into `<name>-h264.mp4`. The
  common entry for assembly is `to_ae_media()`, and it also stands in `api._adopt_inserts` instead
  of `to_ae_image`. H.264 is three times more verbose than AV1 — hence `crf 20`, not 18.
  `core/verify_jsx.py` catches it before AE.
- **The clip scale from Premiere LIES on re-compressed files (2026-08-01).** `_clip_scale` reads
  Motion>Scale from the XML, and it describes the file that lay in Premiere. Shot in 4K, given to
  AE re-encoded to 1080p — `scale=50.4` travels from the XML, and in a 1080×1920 composition the
  person stands half a frame tall. A real case (speaker C): the user spent half a day fixing this
  by hand, adding +98 to EVERY key of the zoom null, and then shrinking the "intro" null to 55–60%,
  because the intro hangs on the null and grew together with it. Therefore the Camera 1 scale

  is computed in the .jsx from the REAL size of the source (`src.width/height` — AE knows it):
  `CAM1_FIT` = % of frame fill, 100 = the frame is filled exactly, regardless of the file
  resolution. The roto copy and its mask are computed from the same number. Camera 2 cutaways still
  take the scale from Premiere — there it usually is a meaningful frame crop.
- **The frame fill is multiplied into the zoom keys exactly once.** The visible frame =
  `CAM1_FIT × CAM1_SCALE`, but since ZE the multiplier `cam1_fit/100` is put into the zoom keys
  THEMSELVES in `scene_plan`, and everyone takes it from there: `CAM1_SCALE`, `_zoom_max` (intro
  auto-fit), `_cam1_follow_keys`, the `cam1_head_min` threshold and the plan. The layers of the clip
  and the roto of camera 1 get a fill of exactly `100`, and `plan["zoom"]["fit"]` is `100` too: the
  previous model (the preview multiplied everything around the push-in point, while AE put `fit` into
  the Scale of layers that grow around THEIR OWN center) diverged from the preview by 240–335 px
  vertically. If you want the previous zoom amplitude — shrink `cam1_zoom_big` / `cam1_drift_hi`.
  The follow threshold is compared with the zoom BEFORE multiplication (`min_scale = cam1_head_min *
  k`): the owner sets it in the same numbers as the jump and push-in ranges and should not
  recalculate in his head. Do not try to make `cam1_fit` an additive null edit: the intro and cam1
  inserts hang on the null, and any null edit drags them along — now the zoom itself drags them, and
  this is expected behavior.
- **The verifier looked only at the FIRST timeline of a splice (2026-08-01).** In "one .jsx for
  everything" the structures repeat N times, while `extract_structs` took the first occurrence of
  `var CAM=` — the remaining timelines went into AE unchecked (the same AV1 insert sat in the second
  one, and the check was silent). Now `verify()` cuts the file by
  `// ===== следующий таймлайн =====` and runs the checks on every piece, marking the messages
  `таймлайн k/N:`.
- **Windows / path case:** `find_cam_dirs` sorts camera folders by the NUMBER in the name
  (`камера1` < `Камера2`), otherwise the case could swap the cameras.
- **Highlight words:** auto-pickup of colored words from Premiere + the `<stem>.yellow.json`
  fallback; the stack break is by a gap of exactly 1 frame; the bar `|` between consecutive
  highlights breaks the stack.
- **arrow.dll crash when torch and the breath detector are combined (2026-09-05).**
  In a Windows process `arrow.dll` crashes with an access violation `c0000005` if `pyarrow`
  is loaded after `torch` through the chain `sklearn → pandas → pyarrow` (pulled by the breath
  classifier in `core/breath.py`). The crash leaves the process with code 0 but cripples
  WinAPI: `subprocess` and file reads return garbage. The invariant: **in any process where `torch`
  and the breath detector live side by side, `pyarrow` must be loaded before `torch` —
  `import arrowfix` does this as the first project import of the entry point.** Three points are
  protected: `core/omni_cut.py`, `core/gigaam_cut/__main__.py` and `tools/train_breath.py`.
  Details and measurements are in `core/arrowfix.py`.
- **Intro text geometry: baseline binding, lowercase letters and the asymmetry of the line step
  (2026-09-07).**
  In After Effects text layers with center justification have `Anchor Point = [0, 0, 0]`, where
  vertically $Y=0$ is the font baseline.
  Capital letters (font size 140 pt at 100% scale) grow from the baseline up by ~100 px (cap-height).
  Lowercase letters at 69% scale (`Scale = [69, 69, 100]`) grow from the baseline up by only ~48 px
  (x-height $\approx 70 \times 0.69$).
  Therefore the visual gap with an equal step of the baseline coordinates is sharply asymmetric:
  * small text ABOVE a big one: `stp = LINE_STEP * 0.75 = 120 px`. The top of the capital letters of
    the big line rises to $Y_1 - 100$, the visual gap equals $120 - 100 = 20\text{ px}$ (tight and
    neat).
  * small text BELOW a big one or two small lines in a row: with `stp = 120 px` the visual gap
    would be $120 - 48 = 72\text{ px}$ (3.5 times bigger — a huge hole).
  A direct measurement of the `MKnew10.aep` reference showed that the real step down to a small line
  is ~72 px, which is exactly equal to `BACK_STEP = 0.45` (`LINE_STEP * 0.45 = 72 px`). With a step
  of 72 px the visual gap below equals $72 - 48 = 24\text{ px}$ (full visual symmetry with the
  20 px above).
  The formula in `core/xml2ae/build.py`: `var stp = LINE_STEP * (GRP[si].back ? BACK_STEP : (GRP[si-1].back ? 0.75 : 1.0));`.
- **Style presets and protection from a stale back_step cache (2026-09-07).**
  In the preset file `styles/Спикер M 2 камеры.json` the value `back_step: 0.75` was historically
  fixed (from the first draft version before the reference measurements). During assemblies
  1522/1523 the speaker style overrode the backend default and generated `BACK_STEP=0.75` again.
  The solution:
  1. In `styles/Спикер M 2 камеры.json` the reference `back_step: 0.45` was set.
  2. In `core/styles.py` an auto-migration was added to `migrate_style_dict`: any `0.75` value in any
     saved style JSON is translated to `0.45` on the fly.
  3. In `core/xml2ae/build.py`, when the scene plan is formed, protection from the legacy `0.75`
     value was added.
- **The "reveal" intro appearance animation: animator isolation (2026-09-07).**
  The reveal animation in After Effects is implemented exclusively through the built-in text animator
  (`Text Animator`: `Scale 3D [11, 11, 91.67]`, `Percent Offset -100 → 100`, form `Ramp`),
  `Gaussian Blur 26.8 → 0` and `Opacity 0 → 100`.
  The `Transform Group > Scale` property of the layer itself must stay static (`[69, 69, 100]` for
  `back`, `[100, 100, 100]` for base lines): keyframe animation on the layer's own `Scale` led to
  words piling onto each other because of a desync between the paragraph width `lineW` and the
  on-screen size of the letters.
- **Synchronizing the intro glitch sound and the Glow parameters (2026-09-07).**
  With `anim == "glitch"` the sound file `gltchgltch_24.wav` is laid on the timeline strictly under
  each glitch word with a lead-in of 17 frames and a tail of 56 frames (at 60 fps). The intro precomp
  with the glitch gets the glow effect `ADBE Glo2` with the values Threshold 211, Radius 93,
  Intensity 0.42 (exactly per `MKnew10.aep`).

### Security (closed 2026-10-07)

The general rule: **names and addresses from the RESPONSE of a foreign service are untrusted input**,
exactly like the request body. One helper for everything — `core/_pathguard.py`: `safe_name()` cleans a
name down to letters, digits, `.`, `_`, `-` (separators, a bare `..` and control characters are
replaced — `../x` becomes `-x`, not an escape from the folder; reserved Windows names like `CON` are
filtered out as well), `inside_dir()` additionally checks the `realpath` of the result inside the target
folder (cleaning the name alone does not catch a symbolic link in the folder itself). The stock file
name assembled from `cand["id"]` gave `id="../../../../tmp/OWNED"` before this fix — the file landed
outside the library.

Addresses — `core/app_meta.unsafe_url_reason(url)`: only `http`/`https`, and EVERY address into which a
name resolves is checked (a single host name is bypassed by a name resolving to a private IP): loopback,
link-local, private, reserved, multicast and undefined — refused. Redirects go through
`SafeRedirectHandler` with the same check, and a request to a foreign host goes out already without
`Authorization`, `Proxy-Authorization` and `Cookie` (`HTTPRedirectHandler` otherwise carries all headers
except `Content-*`). A refusal is a reason not to download, not a crash: the sweep takes the next address,
the paid result is not lost. The guard stands in `core/stock.py`, `core/aicut/images.py` and
`/api/video_probe`; requests to the PROVIDER itself are not touched — its `base_url` is chosen by a
human, and `http://127.0.0.1:1234` (LM Studio) is normal there. The guard is
`tests/test_security_fixes.py`.

- **AI profile headers outward — masked, like a key.** The value of `Authorization` /
  `x-api-key` is as much a secret as `api_key`, while `GET /api/ai_config` gave it to the browser in
  plain text (localStorage, devtools, the "Custom headers" field itself).
  `apply_profile_headers` takes the headers from the profile, and outward they go as `•••xxxx`; on save,
  a value that is still a mask does NOT overwrite the real one (the same
  `resolve_header_mask` door as for the key), while a live call (`Check`, the model list) unfolds the
  mask back. `env:VAR` is not masked: it is a variable name, not a secret.
- **The video card lock does not spin "waiting" forever.** `core/gpulock.py` counted ANY failure as
  busyness — a read-only folder, missing permissions, an `msvcrt` failure: `while True` with `sleep(0.5)`
  and a "waiting for the video card" line, although there was nothing to wait for. Now busyness is only
  a refusal to lock an ALREADY OPEN file (the lock is held by another process, and waiting for it is
  honest), while a file that cannot be opened or created gives a `ReelsiError` with the reason right
  away.
- **`_host_is_local` compares the PORT too.** `127.0.0.1:5999` is a local name, but a foreign door on
  this machine: a Host that named the port must name OUR port (`api/_core.py`).
  The address for the render without AE is assembled from its own port, not from the `Host` header,
  which anyone can set; the own port is taken from `local_ui_port()`, not from the request's
  `SERVER_PORT` (the HTTP server assembles it from the same header).
- **`/api/export_xml` is a POST**, because it writes a file: a GET would be triggered by a foreign page
  with an `<img src>` tag.
- The link to `.github/SECURITY.md` was fixed — it led nowhere.

## How to run / check

- Web: `python reelsi/webui.py` → http://127.0.0.1:5001 (the only interface).
- **Diagnostics and crashes:** the standard log is written to `reelsi.log` (or `$REELSI_LOG`). On sudden
  native failures (CUDA, C extensions) the traceback is written to `reelsi_crash.log` via `faulthandler`.
  At start the server checks the marker `reelsi.<port>.running` (without a port — `reelsi.running`, or
  `$REELSI_RUN_MARKER`): if the previous process on this port crashed abnormally, a warning, the tail of
  the crash log and the Windows EventLog events are output to the log.
- CLI of AI cutting (the main engine): `python reelsi/omni_cut.py --cam1 A.MP4 --cam2 B.MP4 --out cut.xml --mode gigaam`
  (modes `old`/`gigaam`, speaker profile — `--speaker`).
- Classic CLI: `python reelsi/reelsi.py --cams 2|1` (or `--single`, `--no-cut`, `--aggressive`).
- Contract tests: `python -m pytest tests -q` (parse_full, set_highlights/
  edit_word + .bak, /api/status, /api/ui_state). Run after edits to xml2ae/api.
- **As in CI — `python -m pytest tests -q -n auto --dist loadgroup -m "not perf"`.**
  `-n auto` (pytest-xdist) cuts the full set down to a couple of minutes; `--dist loadgroup`
  is mandatory together with it and keeps tests that raise a REAL Chrome
  (`test_ui_static`, `test_wv_ins_modal_geometry`, `test_webrender` are marked
  `xdist_group("chrome")`) in one worker: several browsers at once measure geometry unstably.
  `-m "not perf"` excludes time-budget tests
  (`test_emphasis_prosody`) — they measure the speed of the runner, not the code; locally they run.
  The pair "`-n auto` + `--dist loadgroup`" is guarded by `tests/test_ci_config.py`.
- **The tests also run on the slice itself — `python tools/slice_check.py`** (`--ref <commit>`,
  `--keep` — keep the folder for analysis, `--only <step>` — run one step; the gitleaks binary —
  `--gitleaks PATH` or `$GITLEAKS`, missing — a failure, and `--no-gitleaks` — a loud
  skip): the slice `tools/public_slice.py` is assembled into a temporary folder, and the whole CI is
  repeated in it, not only pytest — `ruff`, `mypy`, `node --check` over ExtendScript and `static/app/*.js`,
  `compileall` with `--help` of the entry points, parsing of `requirements*.txt`, gitleaks over the slice tree
  and the `linux` step in docker with `--init` over ssh (`--linux-ssh USER@HOST`, `$REELSI_LINUX_SSH`,
  `--linux-image reelsi-ci:py310`, `--no-linux`).
  The return code is non-zero if at least one step failed or was not run; part of the acceptance before
  handing out the slice.
- XML edits in place (`set_highlights`/`edit_word`) leave `<файл>.xml.bak`
  (the original from Premiere, once, on the first edit).
- **Checking the `.jsx` without AE — `core/verify_jsx.py`** (the former manual recipe "copy into
  `.js` and run `node --check`" is inside it, plus everything else):
  `python -m core.verify_jsx Reelsi_out` — syntax, parsing of the structures
  `CAM/SUBS/ROTO/INTRO_GROUPS/INSERTS/CAM1_SCALE`, files missing from disk,
  `.webp`/`.avif` and CMYK images in inserts, a mismatch of `t` with the file extension, overlaps of camera
  clips, `ci` out of range, a newline in a word. With `--xml source.xml` it additionally
  compares the number of cameras/words/inserts with `parse_full`. Run after edits to `core/xml2ae/`.
- **Comparing what actually got assembled in AE — `tools/verify_ae.py`.** In AE:
  `File > Scripts > Run Script File...` > `tools/ae_inspect.jsx` (it puts `<проект>.inspect.json`
  next to the .aep, without a dialog), then
  `python tools/verify_ae.py <проект>.inspect.json --jsx <файл>.jsx` — the number of words,
  intro precomps, inserts, roto pieces, camera clips, the presence of nulls. The "one
  .jsx for everything" assembly gives several main comps — then `--comp <имя>` is needed. If the .jsx
  is from ANOTHER clip (the camera files do not match), this is reported in one line, not with
  a dozen discrepancies. The layer order is a WARNING, not an error: the canonical
  order is below, but the project is edited by hand, and a rearrangement is sometimes deliberate.
- **The only real check of jsx animations is a render/import in After Effects.** Everything
  that touches the layout/keys/roto is checked only there; the checkers above catch only
  what is visible by structure. After code edits **the old `.jsx` must be reassembled** — on
  disk they remain the previous ones.

## Backlog

- **Intro above or below roto? (not clarified, 2026-07-27.)** The first end-to-end comparison of an
  assembled project with its `.jsx` (`10-16.aep`, videos ng10–ng16, `tools/verify_ae.py`): the numbers
  match for all seven — words, intro precomps, inserts, roto pieces, camera clips. In
  **ng13 and ng14** the layer "intro text N" lies ABOVE the roto, in the other five — below, as
  written here ("cameras → inserts/intro text → roto → subtitles"). **This project cannot be used for
  checking: it was already edited by hand in AE**, and the rearrangement could have been
  manual. The conclusion for the method: a FRESH assembly must be compared, before edits — otherwise any
  question about the layer order runs into "I do not remember whether it was me or the script". For now —
  a warning from `tools/verify_ae.py`, not an error.
- The `geologica` style — structural gaps (see `docs/ROADMAP.md`): a duplicate transition (2 layers + Luma
  Key), a 3-comp manual intro, roto on video inserts.
- 1-camera projects: all photo inserts become the "Cam 1" style (fly-out from behind the back, roto is
  needed). Open question: whether to make the "Cam 2" style (push-in+blur) on 1 camera — ask the user.
- Trimming sounds (pop/transition) with sliders and listening — planned.

## AI providers: profiles (LM Studio / Claude / OpenRouter / OpenAI-compatible)

Since 2026-08-06 `aicut` is a package, not a 2721-line file. From the outside it is still one
namespace (`aicut.gen_video`, `aicut.begin_call` — seven dozen names
re-exported by the facade), inside:

| module | what is there |
|---|---|
| `core/aicut/config.py` | provider profiles, keys, reasoning and prompt cache tables |
| `core/aicut/prompts.py` | system prompts and JSON response schemas |
| `core/aicut/llm.py` | the model call: stream, cancel, call generations, LM Studio unload |
| `core/aicut/images.py` | image generation for inserts |
| `core/aicut/video.py` | the video model catalog, their capabilities, request checking, generation |
| `core/aicut/commands.py` | the commands themselves: highlights, inserts, intro, plan |

One consequence that is easy to step on: **live provider catalogs are updated by assignment FROM
OUTSIDE** (`/api/ai_models` after the "Refresh list" button), and the addressee must be the owning
module — `aicut.video.VIDEO_MODEL_CAPS`, not `aicut.VIDEO_MODEL_CAPS`. An assignment to the facade lands
on the package, while `video_caps` reads its own globals: the button quietly stops working.

**Provider capabilities come from the catalog and the API response, not from its name (2026-08-26).**
The provider name in a profile decides exactly two things: the API dialect (`anthropic` → SDK, the rest →
OpenAI-compatible `/chat/completions`) and attribution (`HTTP-Referer`/
`X-Title` for OpenRouter). Everything else — what a model can do, whether the provider has an
Image/Video API, which "Smarts" levels to show — is taken from the models.dev catalog
(`aicut/catalog.py::caps`) and from the responses of `{base}/models`, `{base}/images/models`,
`{base}/videos/models`. A new check of the form `provider == "openrouter"` is almost always an
error: it fixes one provider and breaks all the others. Until 2026-08-26 that is how it was:
with a profile with its own URL the model capabilities were empty, the image and video catalogs were not
queried, and image generation went to `/chat/completions`, where FLUX answers 404.

Two traps of this rule:

- `caps(provider, model)` searches the model across the WHOLE catalog if it did not find it under its own
  key (the vendor from the id prefix → openrouter → any provider with an exact match).
  On such a match **the price is extinguished** (`cost: None`): different hosters of the same model
  have different prices, and showing someone else's means lying. An exact price happens only on a
  match under the provider's own key.
- **Our slug `openai` means "OpenAI-compatible, own URL", NOT the OpenAI vendor.**
  In models.dev there is a provider with the same name, and without a separate table the match
  was counted as exact — the OpenAI price list went out (caught in a live run on
  CommandCode: `gpt-5.6-luna` gave 0.2/1.2). Therefore the catalog key is taken from
  `CATALOG_KEY`, where only real vendors are listed.

**User-Agent is mandatory in all outgoing requests (2026-08-26).** The default
`Python-urllib/3.x` is cut by Cloudflare: for `api.commandcode.ai` both `/models` and
`/chat/completions` answered `403` with the body `error code: 1010`, which is why the
"Refresh list" button found nothing and a profile of such a provider was non-working
entirely. Requests are built by `app_meta.http_req()` — it substitutes `Reelsi/<version>`
if the caller did not set his own UA (the browser `_PROBE_UA` for probing foreign CDNs in
`core/aicut/video.py` remains its own). There must be no bare `urllib.request.Request(` in `core/aicut/`, `api/`,
`core/omni_asr.py`, `core/insertlib.py`, `webui.py` — this is watched by
`tests/test_ga_user_agent.py`.

All LLM calls go through `aicut._ask_json()` — it resolves the ACTIVE profile from
`reelsi/ai_config.json` (in .gitignore — it holds API keys; no file → the LM Studio default
from the env `LMSTUDIO_URL`/`LMSTUDIO_MODEL`) and dispatches: `anthropic` → `_ask_anthropic()`
(the `anthropic` SDK, streaming + structured outputs via `output_config.format`,
temperature/thinking are not passed); the rest → `_ask_openai()` (OpenAI-compatible
`/chat/completions`; Bearer key; for OpenRouter — HTTP-Referer/X-Title attribution;
400 on `response_format` → one repeat with the schema in the system prompt;
`ensure_loaded`/`unload_ours` — only with `provider=="lmstudio"`).

**Cancelling an AI call = a generation (`aicut.llm.EPOCH`), not a single flag.** "Stop" tears the fetch at
the CLIENT; the server stream stays sitting in the provider's stream. While there was one global
`CANCEL`, a repeated start (a typical scenario: cancelled medium-reasoning, set off,
started again) set `CANCEL=False` — the old stream lost its cancellation mark, continued to burn the
provider and on completion called `unload_ours()`, killing the generation of the NEW call: the
UI "stuck", the log showed the progress of the old process. Now: `begin_call()` (every
single AI endpoint) and `cancel_call()` (`/api/ai_stop`, `/api/cancel`) increase
`EPOCH` and put the number into thread-local; the stream checks `cancelled()` and dies itself if
its number is outdated; `is_current(ep)` gates the model unload (an outdated stream and a deferred
unload from `ai_stop` do NOT touch LM Studio if a new call has already started on top);
`clear_cancel()` — for calls without a number (a job, `ai_test`, `ai_genimage`: they go in batches,
their generation must not be changed). In `api/_core.py` the counter `AI_ACTIVE`: a new single call waits
for the death of the previous one up to 25s (in the log "⏳ waiting for the previous AI call to finish…").

API (`api/ai.py`): `GET/POST /api/ai_config` (profiles, keys outward MASKED `•••xxxx`;
the mask on save = "did not change" → the key is preserved), `POST /api/ai_test` (a mini-call
`{ok:true}` → `{ok,ms}`), `POST /api/ai_models` (the provider's model list).
UI (webui.py): the MODEL is selected SEPARATELY for each step — the selects
`<select data-prof="cut|yellow|inserts|intro">` ("Cut model" on page 1;
"Model · highlights/inserts/intro" on page 2, next to its own "Smarts ·"). The common
`#aiprofile`/`#aiprofile2` are gone. The ⚙ `mbAISettings` modal (profile CRUD,
Base URL autofill, a datalist of model hints, "Check", "Refresh list")
sets `active` — now it is a FALLBACK profile (a step without its own binding takes it).
The `model` field was REMOVED from the JS fetches — the server resolves the profile of the STEP
itself; the `model` parameter in the endpoints remained as an optional override (CLI/compatibility).
By default only text (words+timings) goes to the cloud.

**The model is also PER STEP** (like "smarts", 2026-07-23). It is stored in
`ai_config.step_profiles` (`{cut,yellow,inserts,intro}` → profile name), empty/none =
the common `active`. It is read by `aicut.step_profile(step)`, passed to `_ask_json(..., profile=)`
and via `resolve_profile(name=)` overrides `active`. All AI calls of a step pass
their profile: cutting (`omni_cut`/`gigaam_cut` decide) → `step_profile("cut")`,
highlights/inserts/intro → their own. The action `/api/ai_config {action:"set_step_profile",
step,name}` (an empty `name` = reset to `active`); renaming/deleting a profile
fixes `step_profiles` (a move to the new name / a rollback to `active`).

**Omni (hearing) is also a profile**: the key `active_omni` in ai_config.json (`"__local__"` =
the local Qwen2.5-Omni-7B, the default; a profile name = a cloud audio model). The select
`#omniprofile` on step 1, the action `set_active_omni` in /api/ai_config (anthropic
is rejected — Claude does not accept audio; deleting a hearing profile resets to local).
`core/omni_asr.py`: heavy imports (torch/transformers) only on the local path;
the cloud path `transcribe_clip_cloud` sends chunks of ~24s as `input_audio` (base64 wav,
temperature 0) to an OpenAI-compatible `/chat/completions` — it works with audio models
(Gemini on OpenRouter and the like). ATTENTION: cloud Omni sends the SOUND to the provider.
`core/omni_cut.py` with cloud Omni does NOT unload LM Studio (no VRAM swap is needed).

**The Omni hallucination filter (omni_cut.py)**: on coughs/sighs Qwen2.5-Omni hallucinates
coherent text ("Это что за шум?", the boilerplate refusal "Извините, но я не могу…") — the decide-LLM
saw "speech" and kept the cough in the cut. Three layers BEFORE the LLM (`halluc_drop`, only
intervals <2.2s): (1) the acoustics `voiced_ratio` — no voice tone (autocorrelation of F0
60–350 Hz, threshold <0.12) = a cough; a sigh WITH A TONE gives 0.3–0.55 and is not caught by acoustics —
it is caught by (2) a repeat of identical text on >=2 short intervals (a chat-prior pattern;
self-learning in `halluc_phrases.json`, .gitignore) and (3) a dictionary of known phrases
(HALLUC_SEED + learned ones). Plus in `is_nonspeech`: a density >25 letters/s (physically impossible)
and <5 letters per ≥0.8s ("Это." over 1s = a sigh). The .omni.json cache is reused —
restarting the cut applies the filter without a second transcription.

**Generating insert images (Nano Banana)**: the key `active_image` in ai_config.json
(`"__off__"` = off, the default; the "Images (generation)" select at the bottom of the ⚙ modal; the provider
anthropic/lmstudio is rejected). `aicut.gen_image()` — OpenAI-compatible
`/chat/completions` with `modalities:["image","text"]`, base64 from
`choices[0].message.images[0].image_url.url`. `/api/ai_genimage {query,dest}` →
`insertlib.add_generated()`: PNG into `<база>/generated/` + a record in the index WITHOUT a rescan
(desc=query, the embedding right away) → the next videos find it by auto-match for free.
UI: a ✨ button on the photo insert card + "✨ Missing" in the inserts modal (the price and the count
in a confirm BEFORE the start, generation is sequential). The priority is always: library → generation.
The recommended model: google/gemini-3.1-flash-lite-image (~$0.04/piece).

**Video generation**: the "Video" tab remains a separate raw tool, while the cards of
video inserts start the same `aicut.video.gen_video` through the same `VJOB`. The profile
(`active_video` in ai_config) = provider + key, and the only model and resolution are
`ai_config.video_model` (`set_video_model`) and `ai_config.video_resolution`
(`set_video_resolution`); all selectors live in ⚙ → "Generation".
The tab shows a summary of this common choice and a link to the settings, so the model and
resolution cannot diverge between manual generation and inserts. The resolution is
an OPTIONAL field (empty/none = the provider decides itself); it is resolved by
`aicut.video_resolution_cfg(model)`: for a known model with non-empty caps only the
supported value is returned (case-insensitively, the canon from caps goes out), a stale one is NOT
sent; an unknown model is passed through. `set_video_model` atomically resets the
resolution incompatible with the new known caps; `set_video_resolution` on a known
model with an unsupported value answers with a structured error without writing.
`/api/video_gen` raw and insert take model+resolution ONLY from config/resolver and
ignore the body `model/resolution/duration/aspect`; `/api/video_models` re-evaluates and
resets a stale server resolution (`video_resolution_sync`), returning the result.
On the "Video" tab `vid_res` is a readonly display of the global value; the resolution is not
saved/restored/sent on the front end.

The card sends to `/api/video_gen` only `query`, the slot `a|b`, the speaker key and
the source `insert_duration`. Python adds `speakers/*.json -> video_prompts`, selects
the duration `ceil(duration_sec)` with a clamp of 3…4 s and the minimum allowed length of the model
not shorter than the target. If a known model does not accept any length up to four seconds,
the request stops before `VJOB` and payment; the timing of the card does not change. The ready
`result.path` is set via `insSetMedia`, and the file is later moved into the library by the usual
assembly. The tab and the card share one start/poll: the result context shows the player or
updates the card without creating a competing second poller. Before the start the card saves an
opaque token; VJOB stores it next to the key and returns it in status, so F5 delivers
`result.path` to the original card (not to the current index). A transport error removes only the
local busy, and a ready result always matters more than the local cancellation flag.

The OpenRouter Video API contract is ASYNCHRONOUS: `POST {base}/videos` → `GET polling_url` up to
`completed` → download `unsigned_urls[0]` (it needs `Authorization`, despite "unsigned").
Its own stream and `VJOB` state — it does not lock cutting.

- **The task history lives on the SERVER** (`_videogen/history.json`, `REELSI_VIDEO_DIR` /
  `REELSI_VIDEO_HISTORY` — like `REELSI_UI_STATE`; `/api/video_history`). Generation
  runs for minutes in the cloud, and during that time the page is closed and F5 is pressed — until 2026-08-05
  only the last result survived, the other videos remained on disk
  nameless, and an interrupted task went unnoticed. A record is written on EVERY status
  transition (`running` → `done` \ `error` \ `cancelled`), so a killed server leaves a
  trace; `vhist_boot()` at start marks stuck `running` ones as `lost`, and the provider's task id
  is taken from the log (`vemit`) — it is used to find the paid video, which lies with the
  provider for ~48 h. `vhist_scan_files()` picks up mp4 files from `out/` that the history does not
  know about (made before it). Deleting a record also takes the file: otherwise it would come back on
  the very next folder pass. The fields of the tab itself (query, references, length,
  resolution, aspect, seed, sound) are in the common UI state, the key `VID`.
- **The model catalog is two-layered.** `aicut.VIDEO_MODELS` — built-in facts about the main
  eight (taken from the live `/videos/models` 2026-08-05): lengths, resolutions, aspects,
  frames, sound, seed **plus what is NOT in the provider catalog AT ALL** — r2v
  references and their limits. `video_caps()` puts the live catalog on top (it is the boss: OpenRouter
  validates by it), `video_model_list()` returns the merged list into the dropdown.
  Therefore the page fields are rebuilt for the model IMMEDIATELY, without the network. The live catalog
  is pulled BY ITSELF, in the background when the tab opens (`vidSyncCaps`, a free GET): it
  refines the restrictions and adds the remaining models (Sora, Wan, Runway…) to the dropdown.
  There is NO "check capabilities" button and no sheets of facts on the page (removed 2026-08-05
  at the user's request): restrictions are not shown but applied — the lists contain
  only what is allowed, the unsupported (seed, sound, reference roles) is disabled.
- **The boundaries on which refusals were caught:** Veo — only 4/6/8 s and 16:9 / 9:16;
  Hailuo 3 — the only resolution 2K; Kling 3 — 720p and without seed; Seedance 2.0 —
  4–15 s and the ONLY one that accepts a VIDEO reference (up to 3, **totally ≤15 s**).
- **i2v and r2v are different modes.** A frame (`frame_images`) and references
  (`input_references`) are not sent together: OpenRouter silently takes the frame, ByteDance
  answers "first_frame + reference_images conflict". A last frame without a first one is not
  accepted. **A frame is an IMAGE** (`image_url`): a video set as the first
  frame is rejected — in the UI a video reference has only one role anyway.
- **`video_check()` — the preflight**, the full list of what will not reach the provider:
  length/resolution/aspect by the model lists, both mode rules, one
  first and one last frame, the counters and the budget of references, an **empty prompt** (needed
  by all models), a **non-https link** (it used to be silently dropped — and the user paid for a
  video without his reference), **a link to a page instead of a file**, **a model not from the
  video catalog** (a non-existent one lay in the profile). A quick run (without the network) — in
  `/api/video_gen`, as a response to the press; the full one (with link probing) — in `gen_video`.
- **`probe_media()` / `/api/video_probe`**: HEAD (Content-Type) + ffprobe by the link →
  `kind` (image/video/audio/**page**), length, frame size, weight. The type must NOT be taken from the
  extension (links often do not have one, and photos and videos go into different fields), the video
  length is needed in advance (otherwise a 400 "video total duration … 15.2 … in r2v" comes after
  sending), and `page` is the most frequent error: a page address instead of a direct link.
  It did not open — empty, we guess by the extension. `resolve_refs()` probes EVERY
  link in `gen_video` before building the payload: the type from the client is only a hint.
  Audio (`kind=audio`) is honestly rejected — it is not passed by the OpenRouter schema.
- **Provider errors are expanded** (`_video_error_text`): OpenRouter puts the upstream answer
  as a STRING in its JSON ("message": "HTTP 400: {…}"), and the UI got a mess of
  brackets; now it is the essence + a hint in Russian (`_VIDEO_ERR_HINTS`).
- **Image formats** (`image_formats`, not described by the OpenRouter schema): Veo does NOT take
  `.webp` — caught by the user 2026-08-05, so Veo has a `strict` list and such a file is
  blocked; Kling/Hailuo have less data — only a warning on the card
  (`video_warnings`): a false ban is worse. The format is taken from ffprobe/Content-Type,
  not from the extension.
- **"Stop" after sending** tries to cancel the task AT THE PROVIDER too (`POST
  /videos/{id}/cancel`, then `DELETE /videos/{id}` — not described by the public schema, but the
  `cancelled` status and the `video.generation.cancelled` webhook exist). It did not accept — that is
  what we write in the error: the task will be computed and billed, here is the link to the result. Stopping
  the polling is not enough — money has already been lost this way (2026-08-05).
- References are READY https links: the provider downloads the file by URL, it rejects
  local/data-URI ("Only HTTPS URLs are allowed"). The caption of a reference is not described by the
  schema — what to take from it the model reads from the PROMPT by the @-tag (`@image1`/`@video1`,
  `_video_prompt` weaves it itself, the user's synonyms are normalized).

**Background removal** (`insertlib.remove_bg`, the `rembg`/u2net package, onnx on the CPU ~1–2 s/piece,
the model ~176 MB is downloaded by itself into `~/.u2net` on the first call): image models return a
subject on a white background, while inserts in the library have an alpha. The key `image_rembg` in
ai_config.json (default ON, the "remove background" checkbox next to the select in ⚙) — the background
is removed right after generation, BEFORE writing to the library, plus cropping of fully transparent
fields (the generator leaves wide empty frames). An empty alpha (the model ate everything) → we return
the source. For an already existing file — `insertlib.strip_bg_file()` / `/api/rembg {path,query,dest}`
(the "Background away" button on the card): a transparent `<name>-nobg.png` is placed right into
`<база>/photos` and entered into the index with description=query, the source in "Downloads" is intact
(we do not breed garbage in Downloads, the clean image is needed in the library). rembg is not
installed → generation does not fail, the image is saved with the background + a `warn` in the response.

**Insert files move into the library by themselves** (`insertlib.adopt`, called from
`api._adopt_inserts`): downloaded an image into Downloads → selected it in the card → **at .jsx assembly**
the file moves (`shutil.move`) into `<база>/photos|videos`, and `media` in the inserts themselves is
substituted BEFORE `to_ae_full`, so the .jsx already refers to the new place. The moment is chosen so
that what moved = what was definitely used: while you are trying out options, Downloads is not touched.
The description in the index = the **insert query** (`desc_src="adopted"`) → in the next videos the file
is found by auto-match without running "Describe with AI". We do not move what already lies in the
library (but we do add it to the index), a name collision → `имя_2.ext`, a missing file is silently
skipped, a move error does NOT fail the assembly. Moves are written to `<база>/_import_log.json` (as in
`import_media`). There is one assembly path — the background `/api/build_run` (the mapping is put into
`JOB["insmoved"]` and returned in `/api/status`); the synchronous `tojsx` and
`tojsx_multi` were removed 2026-07-28 as dead duplicates, the front end did not call them.
The same audit removed `xml2srt` (the `.srt` is already written during subtitle generation —
`core/subtitle_xml.py`, and in the CLI `reelsi.py --srt`) and `pickfile` (a dialog for choosing one
XML; the front end calls `pickfiles`). The front end fixes the paths in ITS three places by the mapping —
`c.inserts`, `c.job.ins` and the live `INS` (`applyInsMoved`, path comparison without regard to case or
slashes), otherwise the next assembly would look for the file at the old address.

**The mask shape is remembered per file** (2026-08-04). Together with the description, `adopt` puts
`mw`/`mh` into the index record — the width/height of the photo insert crop in % (that same "Mask W/H" in
the card). `match_many` returns them next to the path, the front end applies them in `insApplyCrop` —
both on auto-match and on a manual choice via 📚. The meaning: the same image from the library used to
arrive with a 100/100 crop and was re-fitted with scrubbers in every video.
Assembly is the only moment when the shape is definitely known (the user just saw it in the
preview), so we write exactly there and **over** the previous one. We do not write 100/100 (that is
"as the JSX computes itself"), values outside the scrubber range 20..300 are discarded
(`insertlib._crop_of`). `build_index` preserves `mw`/`mh` on a full rescan — like
`rej`/`added`. Video has no mask, `insApplyCrop` does not touch them.

**Why auto-match returned the old image** (found 2026-07-18, fixed):
for a generated file `desc` == the query, so when the same query was regenerated the library ended up
with TWO files with an identical desc → identical embeddings (cos=1.0 between them) →
the same score for any query. A tie on score, a tie on `used` (0 for fresh ones) →
a stable sort returned the one added FIRST, that is, exactly the one the user rejected by regenerating.
Plus `insAfterAI` takes the library BEFORE generation, so the new
generation did not even start. The cure: (1) the `match_many` sort key was supplemented with
freshness — `(-score, -used, -added, -позиция)`, new records get `added`
(a timestamp), old ones fall back to the position in the index; (2) `insertlib.reject(path, query)` —
rejection of the **file+query** pair (`rej: [запрос,...]` on the record), auto-match skips such ones,
while manually via 📚 the file is still available, for other topics too. The rejection is set by itself
when the user presses ✨ or ✕ over an AUTOMATICALLY substituted image (a hand-picked one is not
rejected). `build_index` preserves `rej`/`added` on a full rescan.

**Why auto-match dragged fresh generations instead of the library** (found 2026-07-21, fixed).
Two independent biases, both about WHAT goes into the embedder:
1. **The style drowned the subject.** Our queries are almost all of the form "broken eyeglasses 3d icon", and
   "3d icon" is a third of a short vector. For generated/`adopted` records `desc` = the
   query itself, that is, the same style → they clustered among themselves over the meaning: a measurement on
   the production library gave `fried egg 3d icon` → **broken mirror 3d icon (0.67)**, and
   `empty hourglass` → broken eyeglasses (0.77). The old cosine threshold let such garbage through,
   which is why auto-match looked like "mostly fresh generations": 1004 normally described files
   of the library could not beat 51 query-like records. The cure — the `_STYLE` stoplist
   (`3d|icon|render|stock|photo|footage|illustration|closeup|graphic|style|image`)
   is cut **only from the query**: we search for the subject. In descriptions the style remains.
2. **nomic-embed without prefixes.** The model is trained on the asymmetric pair
   `search_query:` / `search_document:`; without them "a short query → a long description" drifts.
   Now `_emb_queries` / `_emb_docs` (the prefix only for models with `nomic` in the id).

The embedding schema is versioned: `EMB_TAG` (currently `q5`) is written into the index, `_ensure_emb_tag()`
at the beginning of `match_many` silently recomputes the descriptions in one batch on a mismatch (~40 s for
1000 files, once, no full disk rescan is needed — the texts are already in the index). Without an
embedder we do not touch the tag: the token fallback works, the recomputation happens when LM Studio
is started.

**An insert library record has three text fields** (2026-08-22). Before, a record had one
description `desc`, and the "describe everything" run wiped metaphors like `money bills flushed down
toilet`, and they cannot be restored from the picture. Now there are three fields:
- `desc` — what was INTENDED: the original phrase of the scriptwriter. Written by `adopt` (`desc_src="adopted"`),
  `add_generated` (`desc_src="generated"`) and a manual edit (`desc_src="user"`); vision
  (`auto_describe`) NEVER touches `desc`.
- `vis` — what is VISIBLE in the picture: written only by `auto_describe` (vision).
- `ru` — the Russian caption of the insert.

A concatenation of all three goes into the embedder via `_doc_text(desc, ru, vis)`, cleaned of style
and color words by `_subject_text` — the subject, without "3d icon / photo / white". The suitability of a
record's text for search — `needs_text(it)`: BOTH fields are unsuitable (`desc` and `vis`); for
`desc_src="user"` it is always suitable — a human edited it, do not rewrite.

**Migration.** `build_index` transfers the vision descriptions of previous runs: for records with
`desc_src="ai"` the description moves into `vis` (if `vis` is empty), while `desc` is cleared and
`desc_src` becomes `"name"`. It is idempotent: after the very first pass `desc_src == "name"`,
a repeated scan changes nothing.

**Vision refusals** — `_is_refusal()`. An answer starting with "None of the objects...",
"i cannot", "there is no" and the like is not considered a description: the record lands in `needs_vis`
and is re-described by the usual "Update library", and the refusal does not go into the embedding — it is
worse than empty, it pulls foreign queries to itself. In a production run there were 155 such answers out
of 1373.

**Missing files** — the `gone` field (a timestamp). Only the scan sets and clears it; records are not
deleted — they hold `used` and `rej` (history). Matching filters by `gone`, and the disk is checked
only for the winners as `k` is collected.

**Path comparison is via `os.path.normcase`** (the Windows FS is case-insensitive). Without
this the scan marked 58 live records as missing and created duplicates for them; `build_index`
collapses such duplicates (`_merge_prev_records`), taking the maximum of the useful: a suitable `desc`
(`_field_unfit`), the maximum `used`, the union of `rej`, non-empty `ru`/`vis`/`mw`/`mh`, and
the embedding — only if the resulting text matched the text of the winning record.

**Ranking** — `rank = score + lex`. `score` — the cosine over the subject part
(`_subject_text`), `lex` — a lexical bonus `LEX_W = 0.35` weighted by word rarity (idf),
computed over the file name + `desc` + `ru` + `vis`. In the output `score` remains a pure cosine,
`lex` is returned as a separate field. The vector matrix is cached (numpy, `_matrix`) and reset
together with the index.

**The auto-match thresholds live in `insertlib`, not in the front end**: `AUTO_COS = 0.30` (for the token
fallback `AUTO_COS_TOK = 0.5`), `AUTO_LEX = 0.20` — an independent threshold of lexical
matching, the `auto` field in the output. `AUTO_COS` was selected by a measurement on 194 real queries
(73% pass, the garbage `chicken breast 3d icon` → a burger remains under 0.28). The second threshold
appeared because out of 1317 queries 39 had a top-1 with a cosine below 0.30 at `lex >= 0.20`
(a literal match of the file name like "revolver cylinder" → `revolver_cylinder.mp4`),
and such queries were not substituted.

**The speaker style** — the `look` field: the appendix `image_prompts[slot].extra` itself from the speaker
profile, normalized by `_norm_look`; it is written only for generated ones. A rank correction, not a
filter: own style `+0.05`, empty `0` (a shared stock image suits everyone), foreign
`−0.15`; a candidate with a foreign style must remain in the output. The speaker profile is resolved only
in `api/inserts.py`, and an already normalized `look` arrives in `match_many`.

**The vision prompt** was changed for `qwen3-vl-8b` and requires naming the subject without style and colors
(the refusal ban is mandatory: on the production library there were 11 % refusals of the form "None of the
objects..."). Measurement (the `tools/bench_vision.py` bench, 40 files with a known answer): `gemma-4-e2b` 16 %
with any prompt, `qwen2.5-omni-7b` 18 %, `gemma4-e4b-sft` 0 % (does not answer),
`qwen3-vl-8b` 26 % and it reads names from packages.

Left for the future: run `describe_file` over generated/`adopted` images so that they
get a `vis` — then the library becomes homogeneous, and `desc` remains the original phrase.

**The insert type is by file, not by the request** (found 2026-07-21, fixed). The `type` in
`INSERTS` decides everything in AE: photo = a freeze frame with a push-in and a style by the active
camera, video = footage with `Quick 2.mov` + whoosh and `sin`. And the file can arrive as anything (by
hand via "Choose file…", from the AE tab), so `insSetMedia()` in the UI sets `type` by the extension
everywhere a file lands, while `xml2ae.to_ae_full` once more fixes the type by `_is_image(media)` before
laying out the styles — the last line of defense, which also cures old saved projects.

**`type_hint` stays soft — the hard filter was rolled back** (2026-07-30). We tried to cut the pool by
type (asked for a photo — we do not give a video). Rolled back the same day: (1) auto-match hits the mark
on average anyway, and a more suitable video is better than "nothing"; (2) the filter also cut the manual
📚 output — nothing but one type remained among the options, and there was nothing to choose from.
Now: auto-match (`insLibFill`) sends the type as a wish — `insWant(x)`, a missing type
= photo (as in `insGenBatch`); the manual 📚 output (`insLibFor`) does NOT send a type at all and takes
k=8 — there the user looks with his eyes. What actually got in the way was something else, see the next
point.

**An image removed with the cross does not come back** (`noAuto`, 2026-07-30). In the library, when
removed, the file is deliberately NOT rejected (see above — a rejection spoiled the library for other
videos), so on the next pass it is first by score again: the user removed the image, added AI inserts or
edited the description — and got exactly it back. Now `insClearMedia` sets `noAuto` on the insert, and
both AUTOMATIC paths look at it: `insLibFill` (it does not set the file, but still fills `libOpts`
for 📚) and `insGenBatch` (otherwise it silently generated a new one for money). Any conscious choice of
a file clears the flag — `insSetMedia` (a 📚 option, "Choose file…", the AE tab) and `insGenCore` (✨).
The flag lives in the clip state, i.e. it survives a page reload; this is guarded by
`test_cleared_insert_is_not_refilled_by_itself`.

**The insert description is edited in the card** (`.qedit` instead of the former read-only `<code>`):
this is the same description that goes both into the library match and into generation. `insQuery()` on
Enter/focus loss: it resets `libOpts` (the old options are not about this query) and immediately
re-matches from the library via `insLibFill([x])`. A file chosen BY HAND or generated
(paid for) is NOT wiped by a description edit — only an auto-matched one (`libAuto`);
to replace such a file, first remove it with the cross. `insLibFill(arr)` — a common match
by list (one batch request), it sets the file only for those who do not have one; `insLibAuto(clip)`
= a wrapper over it for all inserts of a clip without a file.

**The cross next to the file name** (`insClearMedia`) removes ONLY the image — the insert (timing,
description, mosaic) remains; `libAuto`/`genAuto` are reset, `libOpts` are preserved.
We do not substitute from the library again: if he removed it, it means it is not the right one. Do not
confuse this with the ✕ in the card header (`insDel`) — that one deletes the insert entirely.

**Removing an image != "the image does not fit" (2026-07-22).** `insClearMedia` no longer
rejects the file in the library. Before, the removal of an auto-match itself wrote `rej: [запрос]`
records, and a user who simply cleaned an insert (or removed it entirely) silently spoiled the library —
the rejection surfaced later, in other videos, and looked like "it recorded that the image does not fit,
but I did not touch it". Now the file+query pair is rejected only where it is said explicitly:
✨ regeneration OVER an auto-match (`insGenOne`). Deleting an insert entirely (`insDel`) does not
climb into the library at all — it writes only `ins_rejected` ("this insert is not needed here", it goes
into the prompt of the next markup), and that is different memory.

**Generation right at markup**: the "and generate the missing ones right away" checkbox in the 📚 modal
(localStorage `autocut2_inslib.gen`, visible only when generation is enabled in ⚙, default
OFF — it costs money). The common tail `insAfterAI(clip)` = auto-match from the library → (by the
checkbox) generation of the remainder; it is called from all four paths: "Match inserts", "Add more",
"Mark all" (phase 3) and the markup of one clip. In a batch, on the very first error
(402/429/no key) generation stops — do not hammer N times in one outage.

## Image and video generation

**IMAGES** — `core/aicut/images.py`:
- generation profiles (from `images_profiles.json`),
- `build_image_prompt` — prompt assembly (without the "technical tail"),
- `gen_image` → an API call (a cloud model, for example Nano Banana),
- the caption is added with a space (not a comma) — "broken eyeglasses 3d icon",
- rembg (background cutout) — `image_rembg_on`.

**VIDEO** — `core/aicut/video.py`:
- the video model catalog (`video_catalog.json`),
- request validation (prompt, duration, forbidden tags),
- `gen_video` → an API call of a cloud model,
- `normalize_video_tags`, `resolve_refs` (links in the video), `video_warnings`.

**SLOTS** — slots "1"/"2" are TWO GENERATIONS OF ONE subject, slot `a` — batch.

## API endpoints (summary)

**COMMON** — all `/api/*` are JSON, job subprocesses stream the log via `emit`.

**JOBS** (`api/jobs.py`):
- `POST /api/omnicut_run` — AI cutting (a subprocess).
- `POST /api/run` — cutting (the custom panel / classic, parameters from `cutstages`).
- `GET /api/cutstages` — the list of available cutting stages, defaults and thresholds.
- `POST /api/draft_render` — draft render.
- `POST /api/status` — incremental log polling (`since=N`).
- `POST /api/cancel` — cancel the job.
- `GET /api/clean_tmp`, `GET /api/tmp_info` — cleanup/info of `_tmp`.

**FILES** (`api/files.py`):
- `POST /api/files` — camera/file lists.
- `POST /api/pickmedia` / `pickfiles` / `pickone` / `pickaudio` / `pickdir` —
  native file dialogs (one-off, not stored on the server).
- `GET /api/media` — serves any media file (Range for streaming); `?nobg=1` for an image
  returns `insertlib.nobg_path` (the same `<stem>.nobg.png` cache that goes into assembly), video
  — as is.
- `GET /api/waveform` — the peaks cache for the cut editor.
- `POST /api/cams`, `/api/cammatch` — camera layout, matching cams 2..N by sound.
- `POST /api/cams_make` — creating 1..4 folders `cameraN`/`камераN` by an explicit button on a clean installation (they are not created silently), returns the camera composition.
- `POST /api/newtakes` — the "already cut" filter for the queue.
- `POST /api/clip_delete` — a dry-run or deletion of the XML and the files `<stem>.*` plus a separate `<stem>.jsx` in `jsxdir` without touching camera sources and system files (`files`, `bytes`, `skipped`, `cams`).
- `GET /api/fonts` — the registry of installed fonts (PostScript name + family), on error it degrades to an empty list.
- `GET /api/fontfile/<path:ps_name>` — serving a font file only from the `fonts` registry for an exact preview in the browser (protection from reading arbitrary files).
- `GET /api/music_random` — a random track from the music folder.
- `GET/POST /api/ui_state` — the server-side mirror of the UI state.

**AI** (`api/ai.py`):
- `GET/POST /api/ai_config` — provider profiles/keys (masked).
- `POST /api/ai_models` — the provider's model list.
- `POST /api/ai_test` — a mini LLM call (check).
- `POST /api/ai_yellow` / `ai_inserts` / `ai_intro` — markup; `ai_stop` cancels.
- `POST /api/ai_genimage` — image generation; `POST /api/rembg` — background cutout.
- `GET /api/ai_stats` — a summary of calls from `ai_calls.jsonl` by `(model, step, reasoning)`: medians of input/output/reasoning tokens and time, the share of reasoning `rt%`, the number of successful calls and errors (for the settings window).

**MODEL SERVICE** (`api/model_svc.py`):
- `GET /api/model_cap` — the video ceiling of this machine for the cutting settings: the device
  (`cuda`/`mps`/`cpu`), the slot count and the "auto" flag. It is computed by the same code as the service
  (`model_service.slot_count`), and does NOT start the service and does not import torch.

**EDITOR** (`api/editor.py`):
- `POST /api/editor_load` / `editor_save` — the state of the cut editor, the edit memory
  (restored/deleted).
- `POST /api/edit_word` — editing a word's text; `set_yellow` / `clear_subs` — subtitle
  tags.
- `POST /api/caption` — reading (without `text`) or an atomic write (`text`) of the single caption of the video into `<stem>.caption.json` (enabled by the `caption` style, included in `scene_plan` and the AE assembly).
- `GET /api/words`, `POST /api/xml_state`, `GET /api/omnicut_cuts` — the word list/state.
- `POST /api/breaths` — cutting sighs from `.breaths.json`.
- `POST /api/asr_engines` — the speech engine registry.
- `POST /api/gen_subs`, `POST /api/aicut_preview`, `POST /api/scanxml` — subtitles
  from scratch, a virtual EDL for the preview, a folder scan.

**INSERTS** (`api/inserts.py`):
- `POST /api/insertlib_info` / `scan` / `reject` / `match` / `import` / `describe`
  / `desc` / `items` — the insert library index, matching and description.
- `GET /api/insertlib_describe_status` — the status of the background vision description of the library: `running`, `done`, `total`, `error` and incremental `log`/`log_total` by `since=N`.

**STYLE / SPEAKERS** (`api/presets.py`):
- `POST /api/styles` / `savestyle` / `delstyle` — clip style presets.
- `POST /api/style_patch` — a point update of the fields of a user `styles/<name>.json` while preserving the other settings (it does not change the built-in `base`/`geologica`; autosave of the subtitle line/word settings in the UI).
- `POST /api/speakers` / `savespeaker` / `delspeaker` — speaker profiles.
- `POST /api/terms` — the ASR term dictionary; `POST /api/censor_words` — the censor lists.

**ASSEMBLY** (`api/build.py`, `api/render.py`, `core/render_job.py`, `api/previewproxy.py`):
- `POST /api/build_run` — the AE assembly (`.jsx`).
- `POST /api/scene` — the scene plan for the step-3 preview (all the assembly math without
  roto/`.jsx`); the preview draws it and computes nothing on top.
- `POST /api/cams_load` / `cams_save` / `swap_cam` — the camera layout on step 2.
- `POST /api/export_xml` / `export_drp` — download the timeline as XML / `.drp`.
  **`export_xml` writes a file (it edits the XML for the voice track), which is why it is a POST, not a
  GET**: as a GET it would be triggered by a foreign page with an `<img src>` tag. The front end (the
  clip download button, the download on step 2, the XML format in the download dialog) sends
  the path in the body. `export_drp` takes the same XML as input.
- `POST /api/render_run`, `GET /api/render_status` — the headless AE render (Windows
  only, `aerender`), its own job.
- `POST /api/preview_proxy`, `GET /api/preview_proxy_status` — a 720p 4:2:0 proxy for
  4:2:2 10-bit sources that the browser does not decode, its own `PXJOB`.
- `POST /api/preview_calc`, `GET /api/preview_calc_status`,
  `POST /api/preview_calc_cancel` — computing roto and tracking by the preview button
  (`api/previewcalc.py`): the same `core/xml2ae/precompute.py` as at assembly, a shared task
  lock, its own `PCJOB`. The first request returns "what is already computed" (without a GPU).

**GDRIVE** (`api/gdrive.py`):
- `POST /api/gdrive_download`, `GET /api/gdrive_status` — material by a Google Drive link
  via `rclone`, its own job (it does not take the cutting JOB).

**VIDEO** (`api/videogen.py`):
- `POST /api/video_gen` — start generation; `POST /api/video_cancel` — cancel.
- `GET /api/video_status` / `video_history` — status / history.
- `POST /api/video_models`, `POST /api/video_probe` — the model list, a file probe.

**SYSTEM**:
- `POST /api/cancel` — cancel the job.
- `GET /api/status` — incremental polling (`since=N`).
- `GET /api/clean_tmp` — cleanup of `_tmp`.

## See also

- `README.md` — launch, dependencies, a brief description of the modules.
- `docs/ROADMAP.md` — open directions (`geologica`, the video-use stages) and the history of decisions.
- `docs/CUTTING_SPEC.md` — the FULL cutting architecture (both engines, all stages, VRAM, edit memory).
- `docs/HIGHLIGHT_SPEC.md`, `docs/INSERTS_SPEC.md`, `docs/INTRO_SPEC.md` — point specs of the blocks.
