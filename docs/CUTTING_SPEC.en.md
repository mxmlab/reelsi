# The cutting architecture — the full spec (2026-08-10)

Russian version: [docs/CUTTING_SPEC.md](CUTTING_SPEC.md).

> Engine 1 "Cutting": from raw cameras — a Premiere XML with the silence/takes cut out,
> a synced multicam, a draft mp4. Two cutting branches: the **GigaAM branch**
> (`core/gigaam_cut/`, the main one, word-level pauses and analysis) and the **VAD branch**
> (`reelsi.py`, cutting by sound loudness). Both branches share the input — through
> `core/omni_cut.py` and the single stage registry `core/cutstages.py`. The "AI cutting" button
> runs the standard GigaAM pipeline, and the "Custom" modal lets you flexibly
> enable/disable stages. The old Omni/VAD path (`omni_cut.py --mode old`)
> is kept as CLI-only. The final step is shared: `xmlbuild.build()` + sidecars + (optionally) a draft.
> Cutting does not make subtitles in either branch (the "Mark up everything" step builds them from the ready XML).

## Input / output

**Input:** 1–4 camera files (`.mp4/.mov/.mxf`), camera 1 is the main one (the sound always comes from it).
**Output** (in `outdir`, per clip):
- `NN_stem.xml` — Premiere FCP7 xmeml (a 60fps timeline);
- `NN_stem.project.json` — how to rebuild the XML (cameras, offsets, keep, edit memory,
  `speaker`);
- `NN_stem.cuts.json` — what was cut and why (the AI's decisions, nothing silently);
- `NN_stem.omni.json` — a verbatim Omni transcript by intervals (a cache, the old CLI path);
- `NN_stem.breaths.json` — disputed breaths/"кхе" (P_MARK, see the GigaAM path);
- `NN_stem.draft.mp4` — a 720p draft render (only with Omni review on — the `draft` stage);
- `NN_stem.review.json` — the remarks of the Omni review (EXPERIMENT, off);
- `<outdir>/_tmp/` — temporary files (the proxy map, cleaned by the 🧹 button and before a new cut).

## Cutting stages — `core/cutstages.py` (the single source of truth)

The cutting architecture is governed by a set of stages defined in `core/cutstages.py`
(`STAGES`). The frontend gets their list through the route `GET /api/cutstages` and dynamically
builds the panel of the "Custom" modal (the frontend keeps no copy of the list).

### The stage table (`cutstages.STAGES`)

| Key (`key`) | Name (`label`) | Description / `hint` | Default (`default`) | Dependencies (`needs`) | Branches (`branches`) | Custom panel (`panel`) |
|---|---|---|---|---|---|---|
| `pauses` | Паузы | Вырезать паузы: по словам (`speech`), по энергии звука (`loud`) или отключить (`off`) | `"speech"` | `[]` | `gigaam`, `vad` | `True` |
| `asr` | Распознавание речи | Распознавание слов через ASR. Включается автоматически при необходимости | `True` | `[]` | `gigaam`, `vad` | `True` (только чтение/индикатор) |
| `sense` | Смысл (ИИ) | ИИ-разметка смысловых кусков и удаление неудачных дублей через LLM | `True` | `["asr"]` | `gigaam` | `True` |
| `dedupe` | Правка нарезки кодом | Код поверх решения ИИ: убирает повторы и оговорки, навязывает правило «оставить последний заход», возвращает вырезанное; нужен слабым моделям, с умной только портит (режет перечисления и ролевую речь) | `False` | `["asr"]` | `gigaam`, `vad` | `True` |
| `refine` | Подгон резов | Точная подгонка точек реза по огибающей звука и границам слов | `True` | `[]` | `gigaam` | `True` |
| `breath` | Вздохи | Детекция и вырезание вздохов перед фразами | `True` | `["asr"]` | `gigaam` | `True` |
| `draft` | Черновик mp4 | Рендер быстрого чернового видео `.draft.mp4` для предпросмотра (включается автоматически вместе с Omni-ревью, отдельной галки в интерфейсе нет) | `False` | `[]` | `gigaam`, `vad` | `False` |

### Normalisation and the branch selection rules (`cutstages.normalize`)

- **The branch is chosen by the position of the "Паузы" (`pauses`) stage:**
  - `pauses == "loud"` → the **`vad`** branch (`reelsi.py`, cutting by sound loudness);
  - `pauses == "speech"` or `"off"` → the **`gigaam`** branch (`core/gigaam_cut/`, word-level analysis).
- **The `asr` stage is computed automatically:**
  - It is turned on (`asr = True`) if `pauses == "speech"` or any active stage that requires words is enabled (`needs: ["asr"]` — `sense`, `dedupe`, `breath`).
  - It is not set by hand in the interface.
- **Stages unavailable in the chosen branch** are automatically switched off during normalisation.
- For the `vad` branch the function `cutstages.to_reelsi_opts(stages, thresholds)` forms the options dictionary `opts` for `reelsi.py` on the basis of `DEFAULT_THRESHOLDS` (`model`, `scale`, `vad_thresh`, `min_silence`, `pad`, `cam_return`), hard-setting `subs: False` and `srt: False`. Whisper in `reelsi.py` is loaded only with `dedupe: True`.

## Shared stages (all paths)

### 1. Audio + camera sync — `core/sync.py`
- `extract_audio`: ffmpeg → mono 16 kHz pcm wav (without decoding the video, fast).
  The wav tempos are in `mkdtemp` and are removed on completion (reelsi: try/finally; omni_cut: atexit;
  on "Stop" the directory is cleaned by `_kill_curproc` by the `WORK_DIR` marker).
- `find_offset(wavA, wavB)`: RMS envelopes (~100 Hz) → cross-correlation (FFT) →
  the lag in seconds + confidence 0..1 (the sharpness of the peak against the median).
  `offsets[k]` = how much camera k lags behind camera 1; the time of camera k = t_cam1 − offset_k.
- `video_envelope` — the VIDEO envelope (brightness) for `/api/cammatch` (see "Post-cutting").

### 2. Speech detection — `core/vad.py` (the VAD branch and the old CLI path only)
An energy VAD: RMS over 25ms frames with a 10ms step → dB; the noise floor = the 20th percentile;
speech = the floor + `thresh_db` (default 18). Then: merging pauses < `min_silence` (0.30s),
dropping pieces < `min_speech` (0.20s), padding `pad` (0.08s) with re-merging of overlaps.
**Interval boundaries = silence** — cuts at them hardly ever clip words.
The GigaAM branch does not use VAD: silence is cut by CTC word timings (see below).

### 3. Which sound the cutting listens to — the whole speaker voice chain
The cutting recognises speech from the sound AFTER THE WHOLE SPEAKER CHAIN, not from the raw sound
of the camera: the denoiser (if on) and the enabled VST plug-ins. The code is the same as for the output
(`core/voicefx.py`, `render_cached`, the plug-ins are computed in a child process), and the result
is cached by the source and the chain settings.

The order of fallbacks on failure: a plug-in failed — the cutting listens to the denoise track;
the denoiser failed too — the raw camera sound. The reason goes to the log, the cutting does not crash.
The practical consequence: if the voice has been cleaned of noise, the ASR's word timings are computed from
the cleaned sound, not the noise; if the processing only made recognition worse, it is visible
in the log and can be removed in the "Voice" panel.

## Branch 1: GigaAM whole-file — `core/gigaam_cut/` (the main one, word-level)

The package (split out of the monolith on 2026-08-06): `tune` — thresholds; `takes` — takes and the post-pass;
`asr` — transcription/alignment; `decide` — the decision prompts; `pipeline` — the orchestrator
`run()`. It is chosen by default and with `pauses: "speech"` or `pauses: "off"`. The ASR engine
is taken from `active_cut_asr` (default `gigaam`, only engines with the `cut: True` flag).

The order of the `gigaam_cut.pipeline.run()` steps taking the stages (`stages`) into account:

1. **ASR (GigaAM/CTC) over the whole file** (`asr.transcribe_words_for_cut`, a separate process,
   ~7.5 GB VRAM, it exits — the memory is freed): words with NATIVE word timings
   (`word_timestamps=True` — the text and the timings from one model, wav2vec2/forced-align
   is NOT used). We listen in windows of ~18s (`win=18, search=6`) with the seam at the quietest
   point of the last seconds of the window.
2. **Pauses / silence** (`tune.SILENCE_SEC` = 0.8s): if the stage is `pauses != "off"`, pauses
   between words longer than the threshold are hard cut boundaries and are ALWAYS cut automatically
   (an analogue of VAD), `silence_bounds`. With `pauses: "off"` the silence is not cut (`silence_bounds = []`).
3. **27b decides VERBATIM** (`decide.decide_markup`, LM Studio; the **`markup`** mode):
   - If the **`sense: True`** stage is on: the model gets the WHOLE word-level text and places
     brackets `[ ]` around what to throw away. The alignment of the brackets to our words is difflib;
     what the model rewrote/skipped STAYS (cutting silently is not allowed), the coverage is in the log.
   - If the **`sense: False`** stage is off: the decision step is skipped, and all words are considered kept (`kept = set(range(len(words)))`, `drop = set()`).
4. **A code post-pass** (`takes.postprocess`):
   - If **`dedupe: True`**: cleaning false starts (`find_takes` — the beginning of a segment repeats a recent one, the window `TAKE_WIN` = 12s; we do not touch enumerations — `align.is_enumeration`), choosing the last clean take (`force_takes`), protecting unique coherent speech (`veto_unique_drops`), joining short fragments (`heal_fragments`), cleaning the leftovers (`dedupe_repeats`/`dedupe_fragments`/`drop_truncated`), dropping micro-islands (`drop_micro_keeps`), returning the first halves of enumerations (`keep_parallel_runs`).
   - If **`dedupe: False`** (the default): the mechanical take cleaning is switched off.
5. **Assembling the keep** + a conditional sanity guard:
   - If **`sense: True`**, the guard is checked: if less than 25% of the original speech remains or the keep is empty — `SystemExit` "the AI cut out almost the whole video", and the previous XML is not overwritten.
   - With `sense` off the guard is skipped.
6. **Camera layout BEFORE the fine-tuning**: `align.assign_cameras(keep, N)` by MEANINGFUL pieces (see below).
7. **Fine-tuning the cuts by sound** (`tune.refine_keep`, from the wav itself):
   - If **`refine: True`**: exact boundaries by the sound envelope (`EDGE_IN_MAX`=0.15 / `EDGE_OUT_MAX`=0.35, a quiet attack `ATTACK_MAX`=0.15, holes without speech `HOLE_MIN`=0.15 are cut). It inherits the cameras through `parents`.
   - If **`refine: False`**: the fine-tuning is skipped.
8. **Breaths and "кхе"** (`core/breath.py`):
   - If **`breath: True`**: Silero VAD + CED-tiny + acoustics → gradient boosting (`data/breath_model.json`). `P_CUT`=0.95 — we cut them ourselves (into `cutlog`, the source "вздох"), `P_MARK`=0.5 — we mark them in `<stem>.breaths.json`. The speaker has its own threshold (`breath_p_cut` in the profile).
   - If **`breath: False`**: breath detection is skipped.
   - **A conditional mirror sanity guard:** speech > 30s as one piece — a refusal. It is checked ONLY if `refine` or `breath` is on (a sign that the fine-tuning by sound did not separate the fused speech).
9. **XML** — `xmlbuild.build` + the `.project.json` and `.cuts.json` sidecars. They are written INSIDE the pipeline, right after the XML and BEFORE the draft render. **Cutting makes subtitles only when the second text pass is on** (the section below): then they come from the source, and step 2 does not recognise them again. Without it, cutting makes no subtitles — the "Mark up everything" step builds them through the route `/api/gen_subs`.
10. **The draft** — if the **`draft: True`** stage is on: `draftrender.render_draft(out)`. With `draft: False` the draft render is skipped. At the end — `draftrender.clean_tmp(outdir)`.

## The second text pass — `core/gigaam_cut/textpass.py`, `core/asr_merge.py`

**Why.** The GigaAM CTC model gives exact word timings, but its spelling makes mistakes
("кус" instead of "курс"). Whisper spells better, but its timings are rough: it can swallow
a word, make up a word in silence and collapse repeats, and cutting needs the repeats. So the
rule is: **CTC decides which words there are and where they stand; Whisper only corrects the
spelling.**

**The setting.** `active_cut_text_asr` in `ai_config.json` (⚙ → "Cut" → "Cutting engine (text)").
Empty means off, which is the default. Only an engine of kind `whisper` is accepted (the check
is `cut_text_asr_engine` in `core/aicut/config.py`); another id or an unknown one means off with a
warning. The `cut` flag of the ASR registry does not apply here: it is about the cut, and this
pass needs text. The Whisper catalogue: `large-v3`, `large-v3-turbo` (four times faster than
`large-v3`), `medium`, `small` (`core/asr_backends.py`, `WHISPER_SIZES`).

**The merge rule** (`core/asr_merge.py`, `merge_words`):

- words of the two engines are matched by time within ±0.3 s (`gate`) and by spelling
  (`sim`: lower case, "ё" as "е", no punctuation);
- a matched word takes **the timings of CTC and the text of Whisper**;
- two CTC words that Whisper wrote as one ("по" + "этому" → "поэтому") give one word with the
  Whisper text (`merge`); one CTC word that Whisper wrote as two ("вобщем" → "в общем") is split
  into two along its CTC span in proportion to the word lengths (`split`);
- a CTC word that Whisper did not find (swallowed) **stays as CTC has it**;
- a Whisper word with no CTC counterpart (made up in silence) is **dropped**; a word that is not
  in the CTC list never reaches the output;
- repeats ("чтобы чтобы") **are kept**, because they come from CTC;
- a gap whose alignment table would be bigger than 60 × 60 cells is not aligned: the CTC words
  stay as they are and the Whisper words are dropped.

**Two tapes from one pass** (`core/gigaam_cut/textpass.py`, `text_pass`):

- for cutting — the text in CTC form (lower case, outer punctuation removed): cutting compares
  the words with each other, and a comma from Whisper must not change those comparisons;
- for subtitles — the Whisper text as it is (case and punctuation).

The Whisper model is always unloaded, also when it fails, under the lock "текст нарезки"
("cut text"). The unload is done in its own way, not through `asr_backends.transcribe_words`:
that one unloads the LM Studio models, which a neighbouring clip may be using at the same time.
On any error of the second engine the result is `(CTC words, None)`: cutting goes on with the
CTC text and does not fail.

**What is written at cutting** (branch 1, step 9, `core/gigaam_cut/pipeline.py`):

- the XML with subtitles built from the Whisper words of the **kept** pieces only
  (`cut_subs.subs_for_keep`, `xmlbuild.build(..., sub_words=…)`);
- `<stem>.words.json` and `<stem>.srt` — the subtitles on the new timeline, in seconds;
- `<stem>.srcwords.json` — the words of the **whole** source `{w, start, end}` in seconds of
  camera 1, not only of the kept pieces; it is written AFTER the XML build succeeds, so that a
  failed build does not leave other words next to the old XML;
- `project.json`, field `text_subs: true` — the mark that the subtitles were taken from the
  source. It is a mark, not the current setting: the setting may have been switched off after
  the cut.

Without the setting none of this is written: step 2 adds the subtitles, as before.

**Editing pieces on step 1** (`/api/editor_save` → `core/cut_subs.py`, `subs_after_edit`):

- the words of the **remaining** pieces come from the CURRENT XML — with manual text edits and
  deletions, moved to the new timeline;
- the words of the **returned** pieces and the widened borders come from `.srcwords.json`; the
  cut parts go;
- yellow words are carried over: when the subtitles are rebuilt, a yellow word is searched within
  half a second of its old place (`YELLOW_TOL_FRAMES` = 30 frames at 60 fps);
- if `.srcwords.json` is missing and the edit adds a piece, the subtitles are cleared: step 2 makes
  them again, and the reply explains why;
- without the `text_subs` mark the edit carries the subtitles over from the timeline, as before.

## Branch 2: VAD cutting — `reelsi.py` (cutting by sound loudness)

It is chosen by the position of the **`pauses: "loud"`** stage. It runs the classic `reelsi.py` engine,
whose options are formed by the function `cutstages.to_reelsi_opts(stages, thresholds)`:
`subs: False` and `srt: False` are always set, `dedup` is taken from the `dedupe` stage.

The order of the steps:
1. `core/sync.py` — extracting the audio and syncing the cameras.
2. `core/vad.py` — the energy VAD: cutting the silence by the RMS threshold (`vad_thresh` = 18 dB, `min_silence` = 0.30s, `pad` = 0.08s). With `pauses: "off"` the cutting is not performed (`no_cut: True`).
3. **Whisper** (`core/transcribe.py`): started **only with `dedupe: True`** (to find repeated phrases). If `dedupe: False` and `subs: False`, Whisper is not imported at all, and the cutting passes instantly on the CPU without using VRAM.
4. **Dedupe**: `align.find_repeat_ranges` (exact phrase repeats, keep=last).
5. **XML assembly**: `xmlbuild.build` + the camera layout + the sidecars.

## The old CLI path: Omni/VAD — `omni_cut.py --mode old` (legacy)

Kept for history and available only from the console (`omni_cut.py --mode old`).
The order of the steps:
1. `sync + VAD` → `intervals` — candidate speech intervals.
2. `core/omni_asr.py` in a subprocess (~7.5 GB VRAM): interval-by-interval transcription through Qwen/GigaAM.
3. `core/ssm.py` (CPU): an acoustic search for repeats inside the intervals.
4. Reading the edit memory (`user_overrides` from `.project.json`).
5. `decide()` through the LLM (LM Studio) with prompt hints and post-guards.
6. `keep = intervals − drop` + SSM cutting inside what was kept.
7. Self-check of the joins (`selfcheck.check_and_fix`).
8. The camera layout, the XML assembly, the sidecars and an optional draft.

## The ASR registry — `core/asr_backends.py` (who listens to the sound)

A single registry of engines: `engines()` (id, language, whether it gives a probability, the applicability flags `subs`, `selfcheck`, `cut`) + `transcribe_words(wav, engine=…)`. It is served to the UI through `/api/asr_engines`.

### The `cut` flag (suitability for cutting)

For cutting (`active_cut_asr` in `ai_config.json`, the ⚙ → "Cutting" tab) engines are required that give **exact acoustic boundaries of each word's sounding**:
- **`cut: True`** (suitable for cutting):
  - `gigaam` (GigaAM-v3 CTC, Russian) — native CTC boundaries;
  - `gigaam:multilingual_large_ctc` (GigaAM multilingual CTC, 70+ languages);
  - user CTC models from `data/asr_engines.json` (`kind: "ctc"`).
- **`cut: False`** (not admitted to cutting):
  - `gigaam:v3_rnnt` and `gigaam:v3_e2e_rnnt` — the RNN-T heads give the frame of the token's emission, not the physical boundaries of the word's sounding;
  - `whisper:*` and `whisper.cpp:*` — the word boundaries are approximate/coarse;
  - `omni` — the word timings are built by interpolating the phrase.

GigaAM/Omni/CTC run as SEPARATE processes (the subprocess `core/gigaam_subs.py` and the like) —
a CUDA OOM / a native crash does not kill Flask, and VRAM is isolated.

## Camera layout — `align.assign_cameras(keep, N, return_every=2, big_chunk_sec=6)`

- Camera 1 is the base: the edit starts with it; **a big piece ≥6s is always camera 1**
  (the only exception to the alternation, it may go cam1→cam1).
- The other takes: the least used camera 2..N, not equal to the previous one;
  after `return_every` non-cam1 takes — a return to cam1.
- **A hard invariant: neighbouring segments are different cameras** (except the big-chunk exception).
  With 2 cameras this gives a strict 1-2-1-2.
- Before a big piece the camera "yields" to cam1 only if there is somewhere to step (N≥3).
- In the GigaAM path assign is computed BEFORE `refine_keep` (by meaningful pieces) and is inherited
  by the sub-pieces through `parents` — otherwise the camera would jump on an intake in the middle of a phrase.
- The editor can save a manual layout (`project.json["assign"]`); editing blocks
  in the editor resets it (`assign` is deleted — recomputation).
- **Auto-matching by sound** (`/api/cammatch`): `/api/cammatch` matches the duplicate files of cameras
  2..N by envelopes (`sync.video_envelope`, the threshold `sync.MATCH_MIN`) — the "Match by
  sound" / "Match all by sound" buttons on step 1.

## XML assembly — `xmlbuild.build(cams, segments, offsets, out, assign, …)`

- A 60fps timeline; frames = `round(sec*60)`; `pproTicks = frame*4233600000`.
- For EACH keep segment — a clipitem on EVERY camera video track (V1=cam1 at the bottom…):
  cam1 is always `enabled=TRUE` (the base), camera k>0 is TRUE only where `assign==k`
  (the upper enabled track wins in the display).
- The source time of camera k: `in_k = round((s − offsets[k]) * 60)`.
- Audio: one track per camera (all clips, a static gain); in AE the sound is later
  taken only from cam1. Optionally music as two mono L/R tracks with a gain from dB.
- Subtitle graphics (classic/gen_subs only): track 3, FlatBuffer blobs from the reference
  (`subs.SubtitleBuilder`, the template `data/sub_template.xml`/`data/refblobs.json`), auto-scaling of long
  words (`SUB_FIT_CHARS=14`), censoring (`core/censor.py`: an asterisk on the middle letter according to
  `data/badwords.txt`/`data/okwords.txt`; the lists are edited in ⚙ → "Words", and one's own copy lands
  in `*.user.txt`).
- File names are escaped (`saxutils.escape`); ffprobe is memoised by (path, mtime).

## Draft render — `core/draftrender.py`

`render_draft(xml, …)` through a **map of 720p camera proxies** (not rendering the segments from
the sources): the proxies `pv_*.mp4` are built once per camera (NVDEC + `scale_cuda`,
cached in `<outdir>/_tmp/` — `build_preview_proxy`, the same path as the preview;
a 4K source is decoded in 3.8s instead of 60s), and then the segments are cut from the proxies.
- Codecs by platform (`hw_encoder`): Windows/Linux — `h264_nvenc` → the fallback `h264_amf`
  (AMD) → `h264_qsv` (Intel) → CPU x264; macOS — `h264_videotoolbox`. NVENC may not
  fit next to the LLM — an auto fallback by platform, `--cpu`/`--no-proxy` forced.
- Words — `.ass` (cwd=_tmp, so as not to escape Windows paths); audio — atrim + afade.
- A manual re-render: `/api/draft_render` (a background JOB). There is no button in the interface —
  the draft is rendered only together with Omni review (the `draft` stage); the route remains for
  a manual call.
- `clean_tmp(outdir)` — cleaning `_tmp` (the proxies are kept by default).

## Post-cutting: the editor and the preview (webui, without re-running the AI)

- **Preview (PV)**: `/api/aicut_preview` → virtual_edl → the browser plays DIRECTLY from
  the camera sources (`<video>` + Range streaming), switching layers by segment; the sound is
  always camera 1's video; the subtitles as an overlay. It requires no render. (In the cut editor
  the preview is camera 1 only: camera 2's cutaways are needed only in the step 2 previews.)
- **The cut editor**: the waveform of the whole source (`/api/waveform` → the cache
  `_peaks/`, named by the source version), the keep blocks (green) + what was cut (grey) + the cut-log as markers +
  the orange breath strips from `.breaths.json` (a click cuts them); edge handles, undo.
  Saving → `/api/editor_save`: rebuilding the XML from the blocks (the subtitles — per "The second text pass", when it is on) + **recording the edit memory**:
  restored/deleted ranges (>0.3s, with the text from the transcript) → `user_overrides`
  (up to 50, deduplicated) — they will be read by the next run of the old path (steps 4-5 of path B;
  the GigaAM path does not read them yet).
- **Changing/arranging cameras**: `/api/cams_load`/`cams_save` (assign by segments),
  `/api/swap_cam` (re-sync when a camera file is replaced).
- **Subtitles from scratch**: `/api/gen_subs` — joining the keep audio → the engine from `/api/asr_engines`
  → subtitle graphics into the ready XML (this is a "Markup" step, but it uses the same project.json).

## Edit memory (the full cycle)

| What the user edits | Where it accumulates | Who reads it |
|---|---|---|
| brought back/deleted pieces in the editor | `.project.json → user_overrides` (restored/deleted, t0/t1/text) | the old Omni path on re-cutting: the LLM prompt "do not throw out anything similar" + hard protection of restored from drop (the GigaAM path does not read it yet) |
| deleted an AI insert card | `clip.ins_rejected` in localStorage (up to 30) | all calls of `/api/ai_inserts` → `cmd_inserts(rejected=…)`: the prompt + a hard filter of similar ones (type, start ±2s, query words ≥60%) |
| manual cut edits | the difference of `project.json` newer than `cuts.json` | `tools/train_breath.py` — trains the breath detector on this markup |

## Speaker profiles (`core/speakers.py`, `speakers/*.json`)

The `gigaam_cut.tune` thresholds are not universal constants but a calibration for speaker A
(his speech is 39.5 dB above the room noise, for speaker C — 28.8 dB, a spread of 14 dB;
the hard `ONSET_DB=20` passes ABOVE the median of his speech — hence the profiles).
A profile = JSON in `speakers/` (gitignored): the result folder, the AE preset, overrides of
the thresholds, a correction to the prompt (`hint`). The "Speaker" selector on step 1 → `--speaker` →
`apply_speaker` writes the thresholds into the `tune` globals (that is why the thresholds are read through the module
`gigaam_cut.tune.X`, not `from .tune import X` — a name-bound value
would silently ignore the profile).

## VRAM choreography (16 GB, one heavy model at a time)

```
LM Studio (27b) ──unload_ours─▶ GigaAM v3-CTC (subprocess ~7.5ГБ, вышел=освободил)
      ▲                               │
      └── decide (27b снова) ◀────────┘
             │ unload_ours
             ▼
      Whisper/CTC (self-check, путь B) ── release_model
             ▼
      NVENC draft (~1ГБ; фолбэк AMF/QSV/x264)
             ▼ (опц., выкл)
      Omni-ревью (7.5ГБ, subprocess)
```
The rule: every new GPU step goes through the existing swap pattern
(`aicut.unload_ours` / `transcribe.release_model` / a subprocess like gigaam_subs).
Breath/SSM are CPU and do not touch VRAM. Roto/step 3 models follow the same rules.

## Control and cancellation

- UI: webui step 1; the pair queue (`QUEUE`), the camera folders, per-clip ncams; the "AI cutting" button (GigaAM by default), the "Custom" button (the stages modal `/api/cutstages`), the `chk_review` checkbox ("Omni review" — only with it is the `draft` stage enabled).
- Cutting parameters: the active cutting ASR engine `active_cut_asr` (the ⚙ → "Cutting" tab, only engines with `cut: True`), "Speaker" (`speakers/*.json`), the cutting stages `stages` (`core/cutstages.py`).
- The job: `/api/omnicut_run` (AI cutting) and `/api/run` (custom cutting by stages) → the global JOB/LOCK (one slot). The log is streamed incrementally (`/api/status?since=N`, capped at 4000 lines).
- "Stop": `/api/cancel` — `taskkill /F /T` of the subprocess tree (omni_asr/gigaam are not
  orphaned; the `gigaamcut_*` directory is cleaned by the `WORK_DIR` marker), `aicut.CANCEL` breaks
  the retries, LM Studio is unloaded; it is always executed (including for markup without a JOB).
- Timeouts: ffmpeg — `FFMPEG_TIMEOUT` in draftrender; the Omni/GigaAM subprocesses — their own
  (GigaAM subtitles 30 min), a hanging provider stream breaks by itself.
- Atomic writing of the sidecars — `core/fileio.py` (edits are not lost on Stop).
- VAD parameters/thresholds: `DEFAULT_THRESHOLDS` in `core/cutstages.py` (`model`, `scale`, `vad_thresh`, `min_silence`, `pad`, `cam_return`).

## Known limitations / growth points

- The classic path does not run self-check/draft automatically (only the AI paths; manually — 🎞).
- The GigaAM path: self-check is off (the native CTC timings are exact; the re-listening loop
  was removed on 2026-08-10 — in practice it made things worse); `user_overrides` (the edit memory of
  the editor) is so far read only by the old path.
- `--refine` (forced-align wav2vec2 inside phrases, path B) is off — it clips endings
  on ASR mishearings. In the GigaAM path the fine-tuning by sound is `refine_keep` (step 7 of path A).
- The old path: the timings of the decisions are the VAD intervals; cutting inside fused speech without a pause
  is impossible (SSM partially covers it).
- The `.omni.json` cache is validated only by the number of intervals — changing `vad_thresh`/`pad`
  with the same number of intervals will reuse the old texts (rare, but possible).
- We do not let GigaAM's RNN-T heads into self-check (the emission of a token, not the boundary of the sound);
  for subtitles their words are spread by `_widen` (short ones at 0.04s — a flash).
