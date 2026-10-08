# Yellow subtitle highlights — specification

Russian version: [docs/HIGHLIGHT_SPEC.md](HIGHLIGHT_SPEC.md).

Recovered from a real project (a dump of `tools/ae_inspect.jsx` → `1.json`),
episodes matched by transcripts (`Reelsi_out/*.words.json`).

## Base style (all words)
- Font: `SFPro-CondensedSemibold`, centred.
- Size: **140** (in the project it is 97 at a scale of ~140 % — the equivalent; we bake 140 directly).
- Colour: white `[1, 1, 1]`.
- Appearance: hard cut (no animation), no sound.
- Position anchor (single word): X = W/2, Y ≈ **0.5964·H** (=1145 at 1920).

## A highlighted word
- Everything the same (size 140, font, centre), but:
- Colour: yellow **`[1, 0.9176, 0]`** (RGB 255/234/0).
- "Slide-up" animation: Position travels up from below by ≈0.064·H (~123 px at 1920)
  over **~0.35 s**. Scale is not touched.
- Opacity animation **0 → 100** over the same window of ~0.35 s.
- Ease of both animations: **cubic-bezier(0.35, 0.01, 0.10, 0.99)** —
  in AE terms: key1 out-influence 35, key2 in-influence 90, speed 0
  (the same bezier as the camera zoom; `1−0.10 = 0.90`).
- Sound: the pop `assets → highlight_pop` (`4 type & delete.wav`) at the moment of appearance (startTime = the word's start).

## The stack (consecutive highlighted words)
"Consecutive" = neighbouring words in subtitle order, both highlighted.
- Row r (0-based): Y = ROW1 + r · STEP, where ROW1 ≈ 0.5964·H, STEP ≈ 0.06224·H (~119.5 px).
- Each word slides out into its own row at its own start; **the common out** = the end of the last word of the group
  → the whole stack holds together and disappears at once.
- An example from C1221: "ФИЗРАСТВОР"(row0)+"С САХАРОМ"(row1); "ОТЕК"+"ЛИЦА"; "не"+"досыпали".

## The red accent (for the future, not implemented now)
Dark red `[0.6863, 0.1216, 0.1216]` — a negative meaning ("ПОД БЕШЕНЫЕ ПРОЦЕНТЫ").

## The particle "не"/"ни" before a highlighted word
- The particle "не"/"ни" standing immediately before a word the AI highlighted is coloured
  TOGETHER with it (measured over 236 clips: 135 yellow words with a white "НЕ" before them).
- Prepositions and other function words do not join: the rule is only about "не"/"ни".
- The rule goes verbatim into the AI prompt for yellow markup too (`core/aicut/commands.py`).

## The strength of yellows and the camera zoom (`cam1_yellow_zoom_strong`)
The AI places dozens of yellow words, and a zoom on EVERY one turned the video into a continuous zoom
(the owner's decision of 02.10.2026). That is why the camera has a checkbox **"Strong yellows only"**
(`cam1_yellow_zoom_strong`, on camera 2 — `cam2_yellow_zoom_strong`, both on by
default). On — a zoom is placed on a piece only on the strongest yellows; off —
the previous rule "a zoom on every phrase".

The strength is computed by `core/emphasis.py` and put into the `<stem>.emph.json` sidecar next to the XML. Two
components per word:

- **the emotion of the phrase** — the GigaAM model with the `emo` head (`emo = 1 − p(neutral)` in a 2.5 s window with
  the word at the centre; the owner's measurement: 65 ms per word on GPU, neutral 0.98–1.0,
  emotional 0.77);
- **stress by sound** — loudness (RMS, dB), pitch (`librosa.yin`) and the drawn-out quality
  of the syllable, each as a z-score over NEIGHBOURING words (±5 words, median and MAD). Only
  voiced frames go into the pitch: during pauses `yin` outputs a random frequency, and without a threshold the word's pitch would
  drift onto the noise of the pause.

The sound is the voice of Camera 1 in the SOURCE time under the word (the "edit word → source piece"
mapping is the same as for the voice track). It is read in WINDOWS around the needed words, not from the whole
source: the emotion window — 2.5 s with the word at the centre, the stress window — the word block itself ±5 neighbours;
overlapping windows are merged (`_merge_windows`), so neighbouring words do not cause dozens of
ffmpeg reads of one piece. Pitch and RMS are computed ONCE for the merged window
(`tone_track` → `librosa.yin` without Viterbi), and a word takes a slice of the ready arrays: the per-word
`pyin` (with Viterbi) on a clip with fifty yellows ate 141 s out of 145.

Both components lie next to each other in the sidecar, so the estimation method is switched WITHOUT recomputation
(the style key `hl_zoom_strength`; "by voice" does not load the emotion model at all and writes only
`stress` into the sidecar). A component that is not in the sidecar means the word honestly counts as not computed, and the
rule returns to the previous one, rather than considering the word "weak". The formula version is in the sidecar key:
`EMPH_VERSION` is bumped when the weights and windows change, otherwise the old sidecar would survive
the edit.

The zoom rule (`core/xml2ae/layout.py`, `_yellow_strong_phrases`; the knobs are read by
`core/xml2ae/plan_camera.py`):

- the strength threshold **`hl_zoom_min_pct`** ("Strength threshold, %", default 70) — a percentile of the strengths of the yellows of THIS
  clip: a phrase below the p70 percentile does not zoom at all. 0 — all phrases with yellows zoom,
  100 — only the strongest word of the clip. The strength of a phrase is the maximum of the strengths of its words;
- **`hl_zoom_max_per_piece`** ("Zooms per piece, max", default 2) — how many zooms at most
  in one piece;
- **`hl_zoom_second_min_s`** ("Second zoom — piece from, s", default 8) — a shorter piece zooms
  once, even if there are several strong words: a second cycle does not fit into it;
- the threshold is computed over all the yellows of the CLIP (not the piece): "there are many yellows" is a property of the video, and the cycle
  is not selected by strength — the zooms stand in time order, otherwise the state "the camera is already at its peak"
  would be computed incorrectly. The text stays yellow: the threshold is about the camera, not about the colour.

There is one gate: not computed (no sound, no model, a word added by hand after the computation,
the formula version diverged) — the rule works as before, a zoom on every phrase, and the log gets
the line "the strength of yellows was not computed". The sidecar is recomputed at the end of the AI-yellows step
(`aicut.cmd_yellow`) and lazily at build time (`core/xml2ae/precompute.py`,
`emphasis_precompute`); the scene plan only READS the sidecar (`read_emphasis`) — that door is fast,
it is called on every insert edit.

## Where it is implemented
- `core/xml2ae/build.py` — `to_ae_full(..., highlights=set_of_indices)` builds the layers taking the style/stack/pop into account.
- `core/emphasis.py` — the strength of yellows (GigaAM-Emo emotion and stress by sound) and the `<stem>.emph.json` sidecar.
- `core/xml2ae/precompute.py` — `emphasis_precompute`: one computation door for the build and the preview button.
- `core/xml2ae/layout.py` — `_yellow_strong_phrases` (threshold, zoom limit, piece length) and `_hl_phrase_groups` (a phrase of consecutive words).
- UI — the "Words" panel in the preview: a click on a word = yellow; the indices go to `/api/set_yellow`.
- Indices — 0-based positions of words in the time-sorted subtitle list (as `parse_full` sees them).
