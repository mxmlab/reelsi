# The "text behind the back" intro — specification

Russian version: [docs/INTRO_SPEC.md](INTRO_SPEC.md).

Recovered from comp C1235 (`2.json`). Implemented in `core/xml2ae/`
(`to_ae_full(..., intro=[{text,color}])`) + the intro panel in the preview.

## The input model (UI)
The intro text is taken **from the first words of the subtitles**. Each line = a word count (`count`) + a colour
(`white`|`yellow`). The lines take words sequentially from the beginning. These words are **removed from the subtitles**
(`intro_remove` = their indices), so that they are not duplicated. The highlights are re-indexed for the truncated list.
The colour is per line. The layout is an auto stack in the centre.

Backend: `to_ae_full(intro=[{text,color}], intro_remove=[idx])`. The UI sends the ready line text
(a join of the selected words) + the list of indices to remove.

## The "intro text" precomp (1080×1920)
- Each line is its own text layer: 140, SFPro-CondensedSemibold, centred,
  fill white `[1,1,1]` or yellow `[1,0.9176,0]`.
- Auto stack: Y = H/2 + (i − (N−1)/2)·LINE_STEP, LINE_STEP=160.
- Fade-in **phrase by phrase according to the subtitle timing**, WITHOUT a Text Animator (it crashed on match-names):
  each word = its own text layer. A line is measured as a whole (`sourceRectAtTime`) and word by word,
  and exact intervals are taken from that (SPACE = (line width − Σword widths)/(n−1)); the words
  are laid out in the centre as a single paragraph. A word's opacity 0→100 at its moment from the subtitles (times[k]),
  duration F_DUR=0.3s, ease. The intro duration = max(times) + F_DUR + HOLD + F_OUT (the exit window 0.75 s, the fade-out intro_fade = 0.35 s at the end of the window).
  You edit the timing by moving the Opacity keyframes of the needed word layer.

## Placement in the master
- The precomp layer: `parent = Camera 1` (nulls[0]); position `[0, −520.7894]` locally; scale 96.8 %.
- **Two nulls instead of one (2026-08-11).** The precomps hang not on the camera directly, but on an
  "intro" null (parented to Camera 1's Null, shift `intro_y`, common scale `intro_scale`). The groups
  that appear **on a cutaway** hang on a second identical null — "intro on cam2"
  (the same scale; its own position — `intro_y2`, NOT an addition to `intro_y`; the parent is Camera 2's null when it zooms and "the intro travels with the camera", otherwise not — Camera 1's null is never the parent). Old styles (where `intro_y2` was an addition) are converted on read: `intro_y2 = intro_y + intro_y2`, the label `intro_pos2_v`. The reason: on cam2 the frame is different,
  and the text behind the back wants to be lower, and it could only be lowered together with the whole intro
  of Camera 1. The camera is computed from the moment of the FIRST word of the group (`_active_cam_at`, as for
  `oncam2` inserts) and arrives in the JSX as the `INTRO_ON2` array (0/1 per group).
- It is placed **above its own Camera 1 clip** (like the roto layers).
- Effects: **Glow** (radius 42) + **Drop Shadow**.
- The fade-out at the end: the exit window F_OUT=0.75 s, inside it an Opacity fall 100→0 lasting intro_fade (default 0.35 s) at the end of the window (from max(outStart, outEnd - intro_fade) to outEnd). The duration = the timing of the last
  word of the group (gMax) + F_DUR 0.3 + HOLD 1.0 + F_OUT 0.75 (`core/xml2ae/template.py`).

## The shading under the intro

A soft black shading at the bottom of the frame: the white intro text was unreadable on light clothing, and
the user in EACH of the four videos of `amdi1.aep` put `Shape Layer 1` there by hand.

- **Style keys** (`core/styles.py`): `intro_shade` — a checkbox, default `False`;
  `intro_shade_op` — the layer's opacity, %, default `100`.
- **The numbers** (`core/xml2ae/layout.py`, taken from `amdi1.aep`: the same in all four videos):
  the rectangle `SHADE_W×SHADE_H` = 1416×1052, the rectangle's offset inside the group
  `SHADE_OX/SHADE_OY` = (−20, 610), the fill black, no stroke, `SHADE_BLUR` = 653 (Box Blur).
  What differs between the videos is the layer's position (−6…0, 441…606) and the scale 87…103 %; the constants hold the averages:
  `SHADE_X` = −4, `SHADE_SCALE` = 94, and `SHADE_DY` = −215 — the position followed the intro's height
  ("y = INTRO_Y − 215", `INTRO_Y` being the `intro_y` style).
- **Scale**: the numbers are pixels of a 1080 frame, in the plan they are multiplied by the same rule as
  the insert card and the style dimensions (`layout._px_k`, the function `scale_for` in
  `core/style_geometry.py`): `min(W, H)/1080` — the frame's short side. In 16:9
  (1920×1080) the multiplier is 1, not the previous 1.78: the intro text does not change with the format
  (the style's `size`), and the shading grows together with it.
  `intro_scale` multiplies the layer's scale and the Y offset from `intro_y`.
- **The plan is the only source** (`core/xml2ae/build.py`, `scene_plan`): `plan["shade"]` —
  `None` when the checkbox is off, otherwise `{x, y, scale, w, h, ox, oy, blur, op}`. From there the numbers
  are taken both by `.jsx` (the `INTRO_SHADE` object) and by the preview — there is no second copy of the formulas.
- **The build** (`core/xml2ae/template.py`): with the checkbox on, after the cameras
  `main.layers.addShape()` is created with the name "Intro shading": a vector group → a rectangle
  `ADBE Vector Rect Size` = [w,h] and a black fill `ADBE Vector Fill Color` = [0,0,0]
  (through `_fill_js`, like the other fills),
  `ADBE Vector Position` = [ox,oy], the effect `ADBE Box Blur2` (the radius — by the name `Blur Radius`,
  the fallback `ADBE Box Blur2-0001`), Opacity = op. The parent is the "Camera 1" null (first the parent,
  THEN the position and the scale, as with roto), Position = [x,y], Scale = [scale,scale]; there is no null —
  Position = [W/2+x, H/2+y]. With the checkbox off the substitution is empty: the `.jsx` does not change
  by a single byte (golden).
- **The time is the whole video, not the intro window.** `inPoint = 0`, `outPoint = DUR`: this is how the layer lay in
  all four videos of `amdi1.aep` — the user shaded the bottom of the frame for the whole length, not only
  under the intro text. "Under the intro" here means the place in the layer stack and the height (it follows `intro_y`),
  not the time. The preview shows the shading on the whole timeline too.
- **Layer order.** The layer is created right after the camera clips, so it is above them; everything
  added later (inserts, intro, roto, subtitles, nulls) goes higher, and the layout block
  `LAYER_ORDER` raises the groups to the top and does not touch the shading. The result is exactly above the camera
  clips and under the intro.
- **The preview** (`static/app/85-inserts-view.js`, `ipvShade`): the element `#ipvshade` in
  `#ipvstage` — a rectangle w×h with a black background, its centre at `(x + ox·scale/100, y + oy·scale/100)`
  from the centre of the frame, the same Camera 1 zoom transform as the intro block (`ipvCamChild`), the blur
  `blur·k` (k — preview pixels per frame pixel, as for the precomp shadow's `R`), `opacity = op/100`,
  `z-index:4` — above the camera picture (canvas 3), below the intro and inserts. `plan["shade"] == null` —
  there is no element. An edit of the checkbox/slider reaches the preview the same way as
  `intro_comp_shadow_opacity`: `stEdit()` → `ipvPlanSoon()` → `/api/scene` → `ipvUI()`.

## The intro precomp's shadow: opacity in percent (`intro_comp_shadow_opacity`)

Style keys (the "Camera 1" / "Camera 2" group, `core/style_schema.py`, `core/styles.py`):
`intro_comp_shadow_opacity` / `intro_comp_shadow2_opacity` — **percent 0..100**, default
26.7 (= the previous raw 68 out of 255). Next to them the colour `intro_comp_shadow_fill` and the shift/softness
(`intro_comp_shadow_dir`/`_dist`/`_soft`) — in raw AE units, as before.

**Why they were renamed.** The knobs were called `intro_comp_shadow_op` ("Opacity") with
the label "%", but held a RAW AE value of 0..255: a chosen "50 %" gave 20 %, and the default
68 (27 %) looked like "68 %" — "the shadow gives nothing".

**The conversion — one place.** `core/xml2ae/plan_style.py` multiplies the percent by `255/100` and
puts the ready number into `StyleValues`; from there it is taken both by the `.jsx` substitutions
(`core/xml2ae/plan_intro_tpl.py`) and by the preview plan. The preview has no formula of its own: the conversion of
Drop Shadow → CSS is the common door `aeShadowCss` (see `docs/ARCHITECTURE.md`, "Pitfalls").

**Migration.** `core/styles.py:migrate_intro_comp_shadow_pct` converts the old value as
`op/255*100` exactly once (68 → 26.7, 31 → 12.2, 255 → 100) and sets the label
`intro_comp_shadow_v`; personal style files are not rewritten — the conversion happens on read.
At the defaults the assembled `.jsx` is byte-for-byte the previous one. The guard — `tests/test_intro_shadow_pct.py`.

## Intro width: the margin from the edges and the scale — per camera

The style knobs (the "Camera 1" / "Camera 2" group in the panel, `core/style_schema.py`):

| Key | Meaning | Default |
|---|---|---|
| `intro_margin` | "Margin from the edges, %" of camera 1: on EACH side of the frame, 0…30 | 4.0 |
| `intro_margin2` | the same for groups that landed on a cutaway (camera 2) | 4.0 |
| `intro_fit_max` | "Intro scale, %" of camera 1: the ceiling of ENLARGEMENT, 100…1000 | 250.0 |
| `intro_fit_max2` | the same for camera 2 | 250.0 |

The fraction of the frame width the group is fitted into: `1 − 2·margin/100`
(`plan_style.read_style`), and it is ONE for both modes:

* an **unparented** intro (`intro_cam`/`intro_cam2` = false) — auto-fit in both directions
  (`ds = fit`), capped from above by `intro_fit_max(k)`;
* a **parented** one — shrinking only (`ds = min(gs, fit)`); the camera's zoom does not let it go wider
  than the text; a parented one does not read the ceiling at all.

Which knob is taken is decided in ONE place — the choice of the group's camera in `plan_intro.py`
(the same place as `intro_cam2`/the camera's zoom): for a group on a cutaway — the `*2` keys.
A group with a manual scale (`gs != 100`) is not touched in either place.

Previously the fraction was one for both cameras (`intro_fit_w`, and for a parented one the constant
`INTRO_FIT_W = 0.92`), and the intro on a cutaway came out larger than on camera 1.
Old styles are converted by the migration `styles.migrate_intro_fit_per_cam`:
`intro_margin = (100 − intro_fit_w)/2`, `intro_margin2 = intro_margin`,
`intro_fit_max2 = intro_fit_max` — the look of already assembled videos does not change.
`INTRO_FIT_W` remains only as the fallback fraction of direct `_intro_fit_ds` calls without a style.

## "Big on the left" holds the top of the block

A group whose first line is marked `big` is laid out differently: the big word on the left,
the rest — in a stack on the right (`intro_big_layout`). Vertically it shows not a line but the block
"big + gap + stack", and with the same anchor (`intro_anchor`/`intro_anchor2`) its top slid
down: the big line is sized to the height of the stack and is raised above
the top of the block by the `intro_big_over` knob. The owner saw this as "the group is lower than the others".

Now the top is aligned: `layout.intro_big_top_shift` computes where the top of the same group would be
in the ORDINARY layout (`intro_line_ys` over the same lines), and shifts all the group's `ys` by that
difference. Both layouts stand on one anchor of one group, so the shift is computed directly in
pixels of the precomp — there is no second multiplier, and no second formula in the preview and `.jsx` either: they
read the ready `INTRO_LY` / `plan.intro[].ys`.

The top is taken from the UPPERMOST line (the minimum of cap heights, as in `intro_block_span`), not from
the first line of the stack: in a group with a background line, that one may be the uppermost.
The shift is applied up to the safe zone under `INTRO_SAFE_TOP`: if the aligned block is already below
the line, there is nothing to lower.

## Word timing
Intro words = the first words of the subtitles (they leave the subtitles). The moment each word appears = its
start from the subtitles (the absolute timeline time), so the intro is in sync with the speech. The words of each line
appear independently. A test in AE is needed (the match-names animator cannot be checked without AE).

## AI markup of the intro and accents — calibration against the manual reference (2026-07-30)
Step 3, the "AI intro" button → `aicut.cmd_intro`: the model returns `intro_rows` (the first words of the
video) + `mid_groups` (accents in the middle), the backend filters and lays them out
(`_place_mids`). The thresholds were taken from **comparing the AI proposal (`<stem>.intro.json`) with what
the user actually assembled in `.jsx`** — 9 videos of the set C1387–C1395, 869 s, 148 accents.

What the analysis of the manual reference showed:

| | AI | by hand |
|---|---|---|
| accents | 73 | **148**, of which 132 in windows free of inserts |
| density in free windows | — | 1 accent per **3.8 s** (507 free seconds) |
| words per group | — | 2 (most often), 1 or 3 |
| gap between neighbours | ≥ 6 s (the threshold) | median **4.2 s**, p10 1.2 s, minimum 0.4 s |
| start inside an insert | — | 8 out of 148 (photos), 0 (video) |
| accents in the last 15 s | — | 18 (the final call to action) |

The markup task is formulated as follows: **fill with text behind the back the places where there are no inserts.**
Hence the edits:
- **The "no closer than 6 s by start" threshold is removed.** It threw away 40 % of the markup: running the reference
  through the old filters left 79/148. Instead of it, `INTRO_MID_GAP` = 0.4 s **from the END**
  of the previous group — insurance against two precomps in the frame at once, not a rhythm setting.
  The same run: 127/148.
- **The "covered by an insert" filter is kept** — the user does not aim there anyway (8 hits out of 148,
  all on photos). It cost only 7 % of the reference.
- **The target is computed over FREE WINDOWS, not over the video's length** (`_free_windows` +
  `_free_quota`, `INTRO_MID_PER_SEC` = 4 s per accent of free time). What goes into the task is
  not one number per video, but **a quota per window** ("65–73с → 2"): "about one per N
  seconds" in the system prompt is read by the model as a wish, while a per-window quota it fulfils.
  A check against the reference: the sum of the quotas is 131 against the actual 132 accents in free windows.
- **Empty windows are named individually in the log** (`окно 65–73с осталось без акцента`, the threshold
  `INTRO_EMPTY_WARN` = 6 s) — these are exactly the places the user later goes into by hand.
- **A window is considered free from 2.5 s** (`INTRO_FREE_MIN`); it used to be "longer than 8 s". With 13
  inserts in a 100-second video there are almost no windows longer than 8 s, and the map said "there is nowhere to put it".
  The intro's length for the map is estimated at `INTRO_EST_WORDS` = 14 words: the exact number is known
  only after the model's answer, and the map must be assembled before the call.

The prompt (`INTRO_SYSTEM`) was rewritten by the same reference: a pass over the map window by window, a list of
"what to highlight" with examples from the manual markup (numbers and units, terms, enumeration markers
"first/second", verdicts and prohibitions, risks), a list of "what not to highlight" (a group
starting with a conjunction, a bare verb) and the previous "not at the very end" was removed — the final call to action
("subscribe", "save it", "in the description") is always highlighted by the user.

**Yellow is rare, about 1 in 4** (the user's request: "yellows only for really important stuff"):
a number or a deadline, a verdict or a prohibition, the final call to action. The 75 % of yellows measured against the reference is
**an interface artefact, not taste**: a double click on a word (`aewToggleAccent`) set the accent
ALWAYS as yellow, and all the manual topping up went through exactly that. Now this gesture sets white, and yellow —
deliberately, with the "yellow" checkbox in the group's row.

The intro cap `INTRO_MAX_WORDS` = 24 (was 20): in the reference the intro reaches 21 words.
The accent line length is aligned with the code — `INTRO_ROW_MAX_CHARS` = 9; about the hook's lines and
precomps — the paragraph below.

### Line styling: `_intro_look` (re-taken from the manual edits of 2026-10-02)

A line's styling (animation and effect) is derived from the owner's MANUAL edits, not from
the previous calibration: 12 clips, 400 lines, a match of **82 %** against the previous 57 %.
The single source of truth is `_intro_look` (`core/aicut/commands.py`); both the hook's lines
and the accent lines get `anim`/`fx` from this function, there is no second copy of the rule.

| What kind of line | Styling | Decided by |
|---|---|---|
| `accent`, of ANY length | `glitch` | 108 out of 109 accent lines; previously 3+ words went to `reveal` — the owner does not have that |
| `back` (background), 2+ words | `up` | 10 out of 16 |
| `back`, one word | no animation | the owner does not set `reveal` on back lines |
| a group of 4+ lines (white/yellow) | the first `reveal`, the rest `right`, cascading | 15 out of 17 groups |
| a white (white/yellow) line of 2–3 words | `up` (outside the cascade) | 57 out of 69 |
| a white line of 1 word | no animation | 57 out of 69 |
| a yellow line of 1 word | no animation | 105 out of 124 |
| otherwise | no animation | — |

The "1+1 → left/right" pair rule is REMOVED: the owner has 3 such pairs out of 43, the other 40 pairs
have no animation. The line's position in the group and the group's size are passed into `_intro_look`
as the arguments `group_pos`/`group_size`; without them the group is counted as one line and the "4+" cascade
does not fire. A group is the lines between `break`s (the precomp boundary), the same boundary as
the hook's.

### "не", prepositions and dependent words are not torn from their word

`_intro_fix_prefix` (the hook's lines) does not let a line end on a function word: "не", prepositions
and dependent words go into the line together with their word. For the accents (`mid_groups`) the same
is done by `_place_mids` BEFORE the inserts and the line split, for every colour (the owner's decision,
2026-10-09): a group with a word from `INTRO_PREFIX_WORDS` before it pulls that word in, as a chain
("НИ В КОЕМ СЛУЧАЕ" — the group "КОЕМ СЛУЧАЕ" starts with "НИ"); a group ending on such a word takes the
next word (if the roll has ended, the word is dropped from the end). There is no separate fix after the
layout any more (`_intro_fix_prefix_mids` is removed). `_split_words`, when splitting a long line, does not
cut right after "не"/a preposition and does not leave such a word alone in a chunk; if there is no
admissible cut, the line is not split. The same rule stands verbatim in the prompt (`INTRO_SYSTEM`) — the
model no longer tears the particle off by itself. The previous `_intro_defunc` (a function word not at the
end of a line) remains a separate step and works before the transfer.

### Line glow — only from the style (`intro_accent_glow`)

The glow of the red intro and accent lines is decided by a SINGLE style checkbox **"Glow of
accent lines"** (`intro_accent_glow`, on by default). The `fx` effect dropdown has been removed from the intro line,
the AI does not set the glow (`fx` is empty on all lines), and the `fx` field saved in a line is not read. The scene plan
(`core/xml2ae/plan_intro.py`) sets `fx="glow"` only on accent lines and only according to this
checkbox — the preview and the build read the same number. The question "should this
particular line glow" is no longer asked: a line's glow is a property of the style, not of the markup.

### "Add a word on the left" (`introGrowLeft`)

The button at an intro line takes the PREVIOUS word into the line: `introGrowLeft(rows, i)`
(`static/app/90-ae.js`) — one common function for the lines panel, working on the data.
In an accent `from` is shifted (the line still starts with the same word, but is one word longer on the left). An emptied line is deleted, and the beginning of the group (the precomp boundary, the `break` field)
moves to the next line. The button is present on lines that have a previous word.

**The hook is a separate story, re-taken from the manual `.jsx` files on 2026-08-14.** The numbers above about
accents do not govern the hook: a hook line is 1–2 words up to ~14 characters (the reference's p90 is 13,
max 16), a long word is not cut; a precomp is one phrase, 2–3 lines and 3–5 words, the hook is
3–5 precomps; the precomp boundary is by meaning, a pause ≥ 0.4 s is only a hint. In the model's
schema `intro_rows` now has `break`; the mechanical fallback `_hook_breaks`
(`INTRO_HOOK_ROW_MAX_CHARS = 14`, `INTRO_HOOK_ROWS`/`WORDS`/`PAUSE`) sets the split
itself if the model did not send it.

**The hook's line length comes from the style; a call to action at the end is the last accent (2026-10-10).**
The hook's line length is set by the style knob `intro_row_max` (default 20): `cmd_intro` substitutes it into
the prompt in place of the `<ROW_MAX>` token, and `_wrap_intro_rows` cuts by the same number, so the model and
the code see one knob. A call word at the end ("напишите мне слово «консультация» в личные сообщения") must be
the last accent. If the last 15 % of the words (at least 30) contain a pair of quotes no longer than three
words, `_place_call_word` puts a yellow group on it and removes the accents after it; a call word inside the
intro or under an insert is left alone and logged. The measurement on the owner's data is `tools/intro_eval.py`,
the "призыв" line in the summary.

## Line selectors, animations, effects and the number counter

Each intro line is configured by independent selectors in `introRowHtml`:
- `color`: `white`, `yellow` (shown in the UI as "highlight"), `accent`, `custom` (with `fill: [r, g, b]`). In intro lines the words are rendered in the highlight colour of the style `var(--introhl, var(--subhl, var(--yel)))`;
- `anim`: the line's appearance animation — `""` (fade), `up` (fly-out from below), `left` (from the left), `right` (from the right), `glitch` (a text glitch through a per-character flicker of the letters' opacity; on the AE side the glow of the yellow glitch = the `glitch_glow` setting — built-in Blur + Glo2 or Deep Glow 2, with a message if the plug-in is missing, no fallback), `reveal` (a per-character scale reveal 0→100% left to right plus blur). The `count` value has been removed from the line appearance selector;
- `fx`: visual effects — `""` (none) or `glow` (`Glo2` glow). The `fx` dropdown at a line is REMOVED (2026-10-02): the glow of accent lines is set by the single style checkbox `intro_accent_glow`, the AI does not set it, and the saved line field is not read (see "Line glow — only from the style");
- `accent` / `back`: style roles (the accent font, the background at a 69% point size);
- `is_count` / `cnt_words`: the counter is put on EVERY number word of the line (`isNumberWord`) — the positions of the words with a counter are listed by `cnt_words` (positions inside the line). Each number word shows its own chip `123` (`.icnt`), and a click toggles the counter of that line only (a line can have several counters, the previous rule "exactly one per intro" is cancelled). Without `cnt_words` (old tasks) the counter is on the first number of the line, and the first click on another number materialises the list. The manual precision input is not shown in the UI — the digits after the decimal point (`dec`) are determined automatically from the number: integers -> 0 (`Math.round`), numbers with a decimal fraction (2.5 or 2,5) -> 1 (`.toFixed(1)`), 3.14 -> 2 (`.toFixed(2)`). In ExtendScript (`core/xml2ae/build.py`) the `Slider Control` and the expression on `Source Text` are independent of the string `anim` — the string animations (`glitch`, `reveal`, `up` and so on) and effects (`glow`) are normally applied on top of a line with a counter. In word mode the plan carries `cnts` (`[[position, target, expression, dec], ...]`), and each number-word layer gets its own `Slider Control`; the scalars `cnt`/`cnt_idx` equal the first element of `cnts` and work in line mode (only the first number).
- **Synchronising five doors:** the fields `count, color, fill, anim, fx, dec, is_count, cnt_words, break, from, gx, gy, gs, accent, back` are passed losslessly through `selectAE` (step 3, task → panel), `captureAE` (step 3, panel → task), `aiIntroRun` (AI intro of the current file), `aiIntroAllRun` (batch AI intro) and `resolveIntroFor` (the resolve into the build and into the preview plan); both AI doors read the model's answer through `introRowsFromAI`.

## Frame-by-frame preview in the intro player (`ipvstage` / `#ipvintro`)

In the browser (`static/app/85-inserts-view.js:ipvIntro`) an exact visual match with After Effects is implemented:
- **Frame-by-frame determinism:** the animation parameters are computed mathematically on every frame from the video player's current `tm`, during pauses and scrubbing (in `static/app.css` the conflicting `transition: opacity` has been removed from `.ipvintro span`, and `display: inline-block` is enabled for CSS `transform` and `filter` to work).
- **The animation curve `ipvEase`:** the equivalent of the cubic-bezier `easePair(35, 90)` from AE is computed through `aeEase` and `bezierT/bezierY`.
- **The appearance effects `anim`:**
  - `reveal`: a per-character reveal of the letters' scale from 0 to 100% left to right (`ADBE Text Percent Offset: -100 -> 100`, `Scale [11, 11]`), with an overall scaling of 0.7 → 1.0, a blur of 14px → 0 and an opacity of 0 → 1 over 0.3s;
  - `up`: a fly-out from below `translateY(100%)` → 0 with the `ipvEase` curve;
  - `left` / `right`: a slide `translateX(±100%)` → 0 from its own width;
  - `glitch`: a per-character flicker of the letters' opacity (an analogue of AE's text animator `ADBE Text Opacity = 0`, `ADBE Text Randomize Order = 1`, `Random Seed = 10`), without substituting the letters with random characters;
  - `fade`: a smooth rise of opacity over 0.3s (or 1.5s for a separate counter).
- **Scale and geometry:** in `ipvIntroPos` the exact AE scale factor `0.968` is applied (`iSc = 96.8 * gDs / 100`), thanks to which the preview matches the After Effects composition window 1-to-1. The `back` lines have a reduced point size `calc(var(--introsfs) * back_scale)` (0.69), a tight `line-height: 1.15` and a `margin-top` compensation that removes the gap between the large and the small text. The whole intro group fades out smoothly over 0.75s before the end of its display window.
- **The number counter (`is_count` / `cnts`):** each number word of the line is animated from 0 to its target from `cnts` over `HL_DUR = 1.5s` along the `ipvEase` curve, preserving the automatic precision `dec`, the comma, the thousands separators and the prefixes/suffixes; each word counts from its own `sp.dataset.t`. Without `cnts` (the backend not restarted) — the previous `is_count`/`cnt_idx` logic. The string animations (`up`, `reveal`, `glitch` and others) are applied on top of the number count.
- **Colours and glow:**
  - `yellow`: the colour `intro_hl_fill` or `hl_fill` through the CSS variable `var(--introhl, var(--subhl, var(--yel)))`;
  - `accent`: the colour `hl_fill3` (dark red by default `var(--subhl3, #af1f1f)`);
  - `custom`: the line's individual colour `l.fill` `[r, g, b]`;
  - `white`: the base colour `intro_fill` or `#ffffff`;
  - `glow` / `glitch`: a `text-shadow` glow of the text colour, analogous to the Glo2 plug-in in AE (on the AE side the glow of the yellow glitch = the `glitch_glow` setting — built-in Blur + Glo2 or Deep Glow 2, with a message if the plug-in is missing, no fallback).
- **Separators and chips:** the chips of highlighted words (`.chip.on`, `.chip-cnt.on`, `.icnt.on`) and the active stack separators (`.brk.on`) are coloured in the highlight colour of the current style `var(--subhl, var(--yel))` with an automatic computation of the contrasting font colour (`var(--subhl-tx)`).

