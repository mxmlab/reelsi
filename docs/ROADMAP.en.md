# ROADMAP — open directions and the history of decisions

Russian version: [docs/ROADMAP.md](ROADMAP.md).

## Rotoscope — done
Automated through the `core/roto.py` module (the local GPU model Robust Video Matting, RVM). It cuts the character's alpha mask for After Effects. Details and invariants — in `docs/ARCHITECTURE.md` (the `core/roto.py` row in the module table and the items in the pitfalls).

## Small fixes (done)
- The censor is computed over the full word list (intro words are spoken too) — `censor_source`.
- The text pop is shifted by +4 frames.
- The end of a title word is trimmed to the start of the next one (small "и/в" no longer overlap).

## AI markup — the `core/aicut/` package
LLM calls have been moved into the `core/aicut/` package (`python -m core.aicut`): marking yellow words, selecting and generating inserts, the hook and the intro. Models and keys are configured through provider profiles in the UI (⚙ → "Models and connections", `ai_config.json`). Architecture and contracts — in `docs/ARCHITECTURE.md`.

## Style presets (styles.py + the "Style" UI)
A preset = a dict (base/highlight font, colour, transition/pop sounds, music dB, roto, intro_mode).
Built-in: **`base`** (default, = the previous style) and **`geologica`** (Geologica
Regular/SemiBold, cream accent, always roto). Custom templates — JSON in
`styles/*.json` (the "Save as template" button), not hardcoded. "custom" in the selector
edits every field. Wired into both build endpoints and into the set.

Implemented now (parameterised safely): text font, highlight font+colour (bold through the
bold PostScript variant), transition/pop sound and the video transition (file replacement on the previous mechanics),
music ducking, roto defaults (on/low%/device), intro_mode.

Left for `geologica` (structural, needs a test in AE — a separate piece of work):
- video transition: 2 layers of `transitions.mov` + the **Luma Key** effect (Key Type 2, Threshold 29) +
  a 90° rotation on the second, the `Cinematic Woosh.mp3` sound — for now only the file/sound is replaced on
  the single Add mechanic of the base style.
- intro: **3 separate comps** ("intro text" 1/2/3), in each of them the words are laid out BY HAND over
  free positions (not an auto stack in the centre), size 140, ExtraBold cream highlight.
- photo inserts: slightly different animations/effects, rotoscope is MANDATORY (both on video and on photos).
  DONE (intro): the roto in the intro window follows the camera BEING SHOWN — cam1 in the gaps + cam2 cutaways,
  sandwiched above the intro text layer (`_span_roto_plan`/`_cam_overlaps`; ROTO entries with `ci`+`intro`,
  masks from the source of their own camera, JSX `CAM[ci]`/`nulls[ci]`). Needs a test in AE.
  LEFT: roto on video inserts; pulling INSERT roto through Premiere takes (currently one cam1 clip at a time).
- trimming sounds (pop/transition) with sliders and preview listening — in the custom style.

## The path: lessons from video-use (2026-07-15)

Inspired by https://github.com/browser-use/video-use — agentic editing: the LLM reads a
word-by-word transcript (not video) → EDL → ffmpeg render → self-eval (up to 3 iterations);
the visual composite (frames+waveform+words) is generated ONLY on demand for disputed places;
audio fades at every cut; a session memory project.md. Architecturally we already match it
(transcript-driven omni_cut, sidecar files, a virtual EDL in `/api/aicut_preview`) —
below are the 4 stages that are missing. Limitation: one PC, 16 GB VRAM — everything expensive
is toggleable, and new GPU steps follow the swap pattern (`aicut.unload_ours` /
`transcribe.release_model` / a subprocess like omni_asr): two models do not live at the same time.

### Stage 1 — audio fades of ~10ms at segment seams

**Status: done** (the `audio_fades` key in `core/styles.py:159`, keyframe generation in `core/xml2ae/template.py:458`).

Right now the joins are hard (in `core/xmlbuild.py` there is only a static Audio Levels gain) — clicks
at the boundaries. IMPORTANT: the final render is After Effects, so AE must apply the fades.

- The main place: `core/xml2ae/build.py` / the `AE_FULL` template — put "Audio Levels"
  keyframes on the audio of every camera clip: -inf(≈-48dB) → the working level over ~10ms (0.6 frame @60fps;
  AE supports sub-frame key times), symmetrically on the way out. The boundaries are taken
  from the parsed FINAL XML → the fades automatically follow any manual edit
  of the edit in Premiere.
- Optionally: the same keyframes in `core/xmlbuild.py` (the FCP7 `<parameter>` level supports
  `<keyframe>`) — so that the sound is clean in Premiere too during manual cleanup. Check the import.
- Toggle: the `audio_fades` style flag (on by default). Cost: ~0 (key generation).
- Criterion: an AE render with no clicks at the seams; the keys are visible on the audio layers.

### Stage 2 — cut self-check + draft mp4 as pipeline stages

**Status: partially done** (the draft render `draftrender.py:render_draft` and `/api/draft_render` work; `core/selfcheck.py` exists in the classic path but is off by default in GigaAM — `selfcheck=False` in `api/jobs.py`).

> **Self-check status as of 2026-08-11: OFF and removed from the UI.** The main cutting
> engine is GigaAM, and in it `core/omni_cut.py` returns from the `gigaam_path` branch long before the
> self-check block: the "self-check" checkbox and the "Self-check engine" select did nothing.
> The internal self-check loop of `gigaam_cut` (`max_iter`, GigaAM re-listened to the cut
> and the draft) was removed on 2026-08-10 — by measurement it made the result worse. The current
> cut without self-check is satisfactory, so the controls were taken out, and `selfcheck` is
> `False` by default (`api/jobs.py`). The path code is alive in `core/selfcheck.py` and works for the old
> CLI path. **Bringing it back is a conscious task of this stage: first decide what
> exactly the GigaAM path re-listens to, then bring the checkbox back.** The mp4 draft
> (the `draft` stage) has nothing to do with it and works in both branches.

The order for each clip (checkboxes in webui):

1. **self-check** (right after cutting, BEFORE rendering — the draft is rendered from the fixed version):
   join the keep audio (the mechanics already exist in `/api/gen_subs`) → Whisper → compare with
   the expected text (`<src>.words.json` minus the cut intervals) → a list of "word
   clipped at seam t" → auto-extend ONLY the problematic boundaries (+0.05..0.1s, editing
   the keep in `.project.json` + rebuilding the XML) → 1 repeat of the check (self-eval like in
   video-use, but max 1 iteration). The report goes to `.cuts.json`/the log.
   Cost: ~0 if combined with the Whisper run that generates subtitles (text comparison is
   milliseconds); +1-3 min ONLY when problems are found. VRAM: inside the existing
   Whisper phase.
2. **draft_render** (the final step of cutting a clip): an ffmpeg render of `<stem>.draft.mp4` from
   the virtual EDL. Reuse: move the EDL parser from `/api/aicut_preview` (api/editor.py) into a
   function — segs (camera+source time), audio, words are already computed. The render =
   trim+concat of the camera segments (NVDEC+NVENC, 720p) + the words through ass/drawtext.
   The draft = "look with your own eyes without Premiere" + the input for the Omni evaluation (stage 4).
   Plus a "Draft mp4" button in webui — a manual re-render after edits in the editor.
   Cost: +~1-2 min per 6-minute video; ~1 GB VRAM (the LLM/Whisper have been unloaded by then).
- Criterion: an injected "bad" cut (a boundary inside a word) is found and fixed
  automatically; a `<stem>.draft.mp4` lies next to the XML.

### Stage 3 — rich LLM context + memory of the user's edits

**Status: done** (`user_overrides` is written in `api/editor.py:327`, read and protects pieces in `core/omni_cut.py:551, 858, 1069`).

- Add to the keep/drop prompt of omni_cut (`core/omni_cut.py`, decide): the pause duration
  between intervals + SSM events (breaths, intra-phrase repeats from `core/ssm.py`) — the model
  sees the structure of the takes (an analogue of takes_packed: they have diarisation+events, we have
  a talking head — pauses/events). Cost: ~0, the data is already computed.
- Edit memory: extend `.project.json` with `user_overrides` (the user brought back something cut /
  deleted an AI insert / moved a boundary — the editor already saves the keep through
  `/api/editor_save`); on a REPEATED markup pass tell the LLM: "the user rejected X — do not
  suggest it". Cost: 0.
- Criterion: a repeated markup pass does not suggest what was rejected.

### Stage 4 — multimodal checks through Omni (experiment, OFF by default)

**Status: done (experiment)** (the `core/omni_review.py` module, launched from `core/omni_cut.py:1090` by the `--omni-review` flag and the `chk_review` checkbox in `templates/index.html:103`).

Qwen2.5-Omni-7B is already installed locally (`core/omni_asr.py`) and is multimodal — it is used only
as an ASR. The timeline_view idea from video-use: on demand generate a composite (an ffmpeg tile strip of frames
+ waveform + words) for DISPUTED places and ask Omni:
- the camera choice on a long piece (who is more expressive on screen — instead of blind alternation);
- a check that "the insert does not cover a gesture";
- evaluating the draft render from stage 2 (watching draft.mp4 piece by piece).
Do NOT run frames of the whole video (the video-use lesson: 30k frames = 45 million tokens).
Cost: minutes + a 7.5 GB swap — an explicit button only. Depends on stage 2.
Prototype criterion: the camera choice on 1 task is no worse than the current alternation.

### Cleaning up temporary files

**Status: done** (auto-cleaning of `_tmp` before cutting in `api/jobs.py:270`, the `POST /api/clean_tmp` route and button in `api/jobs.py:528`, `draftrender.clean_tmp`).

- All new temporary artefacts (draft mp4, self-check audio joins, Omni composites) go
  into ONE folder `<outdir>/_tmp/` (not %TEMP%), with names based on the input hash.
- The "Clear temporary" button — the broom in the webui header: show the size, clear
  `_tmp/`; the roto cache `roto/_cache` (this is a CACHE: deleting it = recomputing RVM) is cleared by the
  API when `roto=true`, but the button does not pass it — half-done, to be finished.
- Auto-cleaning of `_tmp/` when a new cut starts into the same outdir.
- The wav tempos of the cut itself are already cleaned (the 2026-07-15 audit: rmtree in `process_pair`).

### Order

Stage 1 — right away (cheap, sound quality). Stage 2 — the core (self-check and draft_render can be
done independently). Stage 3 — independent. Stage 4 — research after stage 2.

## Parallel cutting of several videos (idea, 2026-09-22)

**Status: open.** A measurement on 12 videos: the decision step takes 40 s per video, and this is NOT generation —
at the `low` level the model thinks half as much (2855 output tokens versus 5176), while the time is the same (38 s
versus 40). So the time goes into the queue and the provider's network, and no prompt cures it
(five prompt variants did not beat the original, details in the `model_bench_2026/typesafe_cut` bench).

Videos are independent of each other, so the only lever is to run the decision step for several
videos at once. On a series of 20 videos, four streams give about a fourfold gain
in the time of the whole batch.

What has to be decided before implementing:
- where to parallelise: batch cutting of several files, not pieces of one video;
- the job lock is currently ONE for a heavy task on purpose (one GPU, VRAM overflow hangs Windows) —
  ASR and rendering are still one at a time, only the cloud decision step is parallelised, and it takes no GPU;
- the ceiling of simultaneous requests to the provider and the behaviour on 429;
- cancellation ("Stop") must take down all streams of the batch, not the current one.
