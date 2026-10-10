# Photo/video inserts — specification

Russian version: [docs/INSERTS_SPEC.md](INSERTS_SPEC.md).

Recovered from a dump of the AE comp **C1221** (`1.json`). Defaults = the averages over 1221.
Implemented in `core/xml2ae/build.py` (`to_ae_full(..., inserts=[...])`) + the inserts panel in the UI.

Each insert: `{type, style, media, start, end, scale, sc, x, y, mw, mh}` — the start/end timing in seconds.
`x`/`y` — the shift of the **rest point** in px (0 = as before): the entrance/exit are computed
from it, and the starting point of the fly-out from behind the back (cam1) does not move. For video it shifts the frame as a whole —
a landscape video, when filled by height, spills out by width, and the centre is almost never what
needs to be shown.

## Photo scale — computed from proportions, not a constant
It used to be flat: `50` for cam1 / `44` for cam2. At 16:9 that gave a card 295 px high
("they all look small"), for a square — 540, that is, the size on screen jumped threefold depending on
which picture the AI brought. Now `_ins_scale()` fits the **visible** part of the photo into
the card `INS_CARD_W × INS_CARD_H` (1030 × 560 px; for cam2 the height is `INS_CARD_H_CAM2` = 495,
preserving the previous 44/50 ratio):
- non-ultrawide (aspect ratio ≤ `INS_MASK_SQUARE_AR` = 2.2) — the mask cuts **into a square**:
  the visible width equals the height, and it rests on `INS_CARD_H`;
- ultrawide (schematics, arteries) is not cut into a square — the meaning of the picture would be lost, it rests on `INS_CARD_W`.

The card numbers and the insert animation (the box itself, `INS_BLUR`, `INS_RISE_DY`, `INS_C1_LOW/HIGH`)
are conceived in pixels of a 1080 frame and are multiplied for the video's frame by the rule `layout._px_k` —
`min(W, H)/1080`, the same as for the style's `size` kind (the function `scale_for` in
`core/style_geometry.py`).
For all four video formats the multiplier is 1; in 4K vertical 2160×3840 — 2, otherwise in a
four-times-larger frame the card would stay half as small as intended. The same numbers travel into
the plan as the `ins_box` field, so that the preview does not keep its own copy.

Checked against the manual fine-tuning of 26 inserts in `1349-54.aep`: the average error is 8.9 scale points,
18 out of 26 within ±10. The rest is taste (how large the object sits inside the frame);
a rebuild does not overwrite a manual `scale` edit if the `scale_manual` flag is set.

## Manual scale — `sc` (% of auto, default 100) — and the animation from it
The **"scale"** scrubber in the insert card (10–400 %, like X/Y). It is a MULTIPLIER on top of auto, not an
absolute `scale`: for photos `scale` is recomputed from the picture's proportions on every build,
and an absolute number from the UI was simply overwritten there (the "Scale %" field on the AE tab did not
work for a single photo insert for years — nobody ever set `scale_manual`).
- **photo**: the settled scale = `_ins_scale() × sc/100`;
- **video**: frame fill `× sc/100`. 100 = full screen, as before; less — a card.

**The animation is computed from the settled scale, not from constants.** The cam2 zoom used to start from
a fixed `INS_C2_PEAK` = 100 %: a card larger than 100 % was not enlarged by the "zoom" but
reduced — the insert inflated inwards instead of the familiar punch. Now the peak =
`S × INS_C2_PEAK / INS_C2_BASE` (BASE = 44, the historical base), that is, the same ~2.27×
of the final size for any `sc`; at `sc=100` the numbers match the old ones exactly.
Blur/opacity and the cam1 fly-out do not depend on the scale.

## Manual mask shape — `mw` / `mh` (% of auto, default 100)
The auto square does not suit every picture: a wide object loses its edges, a tall one keeps
empty air. The **"Mask W/H"** pair in the insert card (a scrubber, like X/Y; a double click —
number entry, range 20–300 %) multiplies the computed width and height of the mask. Larger than the photo
itself the mask does not grow — beyond its edge the precomp is empty, so `mw` rests on the width of the comp,
`mh` — on the visible height of the photo. The scale (`scale`) is NOT recomputed here: a mask at 130 %
by width = a card on screen 1.3 times wider, with the object's size unchanged. If you want the size too —
edit `sc` next to it. The preview in webui shows the same box (`insPreviewBox`),
so you can aim right on the frame. Video has no mask — the fields are shown only for photos.
It works with `insert_fx="card"`: the old kind (`white`) has no mask at all.

**The mask shape is remembered per file** (2026-08-04). At build time `insertlib.adopt` puts
`mw`/`mh` into the library index record, `match_many` gives them back, the frontend applies them
(`insApplyCrop`) both on auto-selection and on manual selection through 📚. The same picture
from the library no longer arrives with a 100/100 crop. Details — in `docs/ARCHITECTURE.md`.

## The AI's field order: the Russian caption first, then the English query
The fields of the structured response are generated **in schema order**. While `query` came first in
`aicut.INSERTS_SCHEMA`, weak models (noticed on DeepSeek,
2026-08-04) wrote an English stub — "cup", "keys" — and the intelligible Russian caption
`prompt` was composed under it. With such a query both the library and the image generator produce
anything at all: you cannot tell WHAT object was intended. Now `prompt` is declared before
`query`, the prompt requires "query = the FULL translation of prompt, not its abbreviation" and forbids
a one-word query (a noun + a qualifier is needed). Ones that slipped through anyway are not fixed silently
(there is nothing to translate with), but shown in the log next to the Russian
caption: "⚠ a stubby query "cup" (~34с) — intended in Russian: …".

The same `query` goes to a stock as the search string, so for a stock the prompt asks not for a full
translation of the caption but for the object and one or two visible features in English, without
numbers, doses or labels. Measured: "syringe with small 250 mark" on Pixabay gave motorcycles. The short
variant of the query (`simplify_query` in `core/stock.py`) is not switched on: on the owner's 18 queries
it was no better and lost the object.

## Forbidden zones in AI selection — the insert is deleted, not moved
`aicut._apply_zones()`. After `_snap_to_phrase()` the insert's timing is tied to the quoted
phrase, so **it must not be moved at all** — it can only be dropped entirely.
There used to be `s = max(s0, 6.0)` here plus a cascade of "no closer than 2.5 s": an insert about a phrase from
the 2nd second moved to the 6th, where different text is already sounding, and dragged its neighbours with it —
the whole "train" of inserts at the start of the video landed meaninglessly.

An insert is dropped if: the start is earlier than `INS_ZONE_PHOTO` (6 s; video — `INS_ZONE_VIDEO`,
10 s) · closer than `INS_MIN_GAP` (2.5 s) to a neighbour or to one already selected (`avoid` during topping up) ·
less than `INS_MIN_DUR` (1 s) remains until the end of the video. At the very end the **duration** is trimmed,
the start stays in place. The main set is limited by `INS_TARGET` (13): no more than 10 photos and
3 videos. After the zones and the edit memory, `cmd_inserts` tops up the missing types with a separate
request for other places; if the top-up fails, an honest incomplete set is returned
(recursion exactly one level, the sidecar is not overwritten).

## Inserts straight from the Premiere timeline (auto)
Instead of entering inserts in the UI, you can lay them out in Premiere on **separate video tracks above the cameras**.
The layout is **deterministic by camera count** (the camera selector in section 1 = for the whole script), `ncams=N`:
- Tracks **1..N** (bottom-up, ignoring the subtitle track) = **cameras 1..N**.
- Track **N+1** = photos, **N+2** = video (in fact everything above the cameras is an insert, the type by the file extension).
- The subtitle track is found by content (GraphicAndType) anywhere and is not counted.
- **The photo style is auto** by the active camera at the start: camera 2's clip is on → `cam2`, otherwise `cam1`. Video — full screen.
UI inserts and track inserts add up. If `ncams` is not set — the fallback heuristic (one file × many clips = a camera).

The timings in the UI are **start (sec:frame) + duration (sec:frame)**, the default duration of a photo 2 s
(the frames are divided by the project's fps). The photo precomp is 1080×1920, the picture inside is contain.
**Snapping the start to the cut:** if an insert starts right BEFORE a camera change (≤ `insert_snap_start`,
default **0.35 c**), the start is moved exactly onto the cut — the insert begins already on the next frame, the whole
entrance animation (both cam1 and cam2) runs on it, and the photo style in "auto" mode is chosen by the NEW camera.
The end stays in place (the window is simply shorter). It is turned off by the same `insert_snap_cut=false`.

All the inserts' keyframes are Bezier along cubic-bezier(0.35,0.01,0.10,0.99) (out-influence 35, in-influence 90). The assets (Quick2/whoosh/riser/pop)
are looked for next to the XML, and if they are not there — in `assets/` next to the Reelsi installation.

Extra auto behaviour: a gif → `loopOut()` (Time Remap); any photo → `wiggle(1,15)` on Position.

## A photo above Camera 2 — `style="cam2"` (scale + blur + opacity)
The precomp layer: the position **540 / 330** (= W/2, H·0.172) + `x`/`y`, 4 keyframes.
If several cam2 photos hang in one window — they simply lie on top of each other: the upper one
(added later to the layer stack) covers the lower one, as in the preview too. Previously such
inserts spread out along X — removed 2026-08-08 (see "Overlapping inserts" below).
The position is static (taken at the moment of its appearance):
keyframes of position would be overwritten here by the `wiggle(1,15)` expression.
- **Scale**: `100` → `S` (entrance) → hold → `S` → `100` (exit). `S` = the Scale % field (default **44**).
- **Opacity**: 0 → 100 → 100 → 0.
- **Fast Box Blur** (`ADBE Box Blur2`, Blur Radius): 41 → 0 → 0 → 41.
- Entrance ≈ **0.38 c**, exit ≈ **0.47 c**.
The effects are controlled by the preset field **`insert_fx`** (the "Photo effects" select in the UI, shared by cam1/cam2):
- **`card`** (default): **Drop Shadow** black (opacity 49%, dir 135, dist 15, soft 70) + on the precomp layer
  a **"Rounding" mask** — a rectangle along the photo's bounds with rounded corners (radius `INS_MASK_R`, default 60 px).
- **`white`** (the old kind, comp 1221): **Drop Shadow** white (cam2: opacity 255, dir 135, dist 0, soft 287;
  cam1: opacity 7) · **Simple Choker** (cam2 −87.2, cam1 −61.6). No mask.

Plus always: **Fast Box Blur** (animated, cam2 only) · **Mosaic** 64×64 — the effect is always attached,
**enabled only if the "mosaic" checkbox is on**.

## A photo above Camera 1 — `style="cam1"` (fly-out from behind the back, needs ROTOSCOPE)
**Parented to the "Camera 1" Null** (`nulls[0]`) — it moves/zooms with camera 1's frame.
The coordinates are local: only **Position** `[0, +464.7]` → `[0, −393.3]` → hold → back. Entrance 0.68 c, exit 0.80 c.
Effects: the same two `insert_fx` modes as cam2 (card: black shadow + "Rounding" mask; white: white shadow opacity 7 + choker −61.6).
The layer is marked with a label colour (`label=11`) and the name `РОТО ↓ <файл>` — you do the rotoscope of the person's mask yourself.

**Overlapping inserts — without spreading out.** If several cam1 photos hang at the same time, they do NOT
spread out: each next one lies on top of the previous one (a layer added later is higher in the stack),
and the rest point is the same for all (`x`/`y` from the card). The X spread (`INS_C1_SPREAD`) was removed 2026-08-08:
an overlapping neighbour that had not yet left the screen moved sideways in the middle of its fly-out, and the timings
of inserts are edited for "one after another". In the "Inserts" window preview the overlap is visible in the same way:
ALL active inserts are shown at once, the upper one over the lower ones (cf. `ipvOverlay` in
`static/app/85-inserts-view.js`).

### The same style on a cutaway — its own null and its own shift
The "Photo insert style" in the UI can be forced to **Cam 1** for the whole video; then the fly-out from behind
the back also goes to inserts shown over **Camera 2** (in "auto" mode this never
happens — above a cutaway it is always `cam2`). Such an insert is marked with the **`oncam2`** flag (computed by
Python from the active camera at its start, already AFTER snapping the start to the cut) and differs in two things:
- it hangs on a separate null **"cam1 inserts on cam2"** (`insNull1b`) — free, in the centre of the frame.
  Parenting to Camera 1's Null would drag its zoom drift of 100–160% along, although Camera 1 itself is not in the frame
  at that moment: the photo crawled and changed size on its own, and there was no way to fix it;
- the rest point is shifted by the common pair **`INS_C1_ON2_X` / `INS_C1_ON2_Y`** (the preset fields
  `insert_c1on2_x` / `insert_c1on2_y`, "Fly-out on a cutaway, X / Y" in the UI — the field is shown only
  with the forced "Cam 1" style). This shifts **all** such inserts at once, in px: X to the right, Y down.
  It adds up with the insert's personal `x`/`y`; the starting point of the fly-out, as with ordinary cam1, does not move.

You can move them all at once by hand in AE too — by the "cam1 inserts on cam2" null.

## Video — `type="video"` (full screen + transitions)
A full-screen layer (scale to fill × `sc`), in/out = start/end, **the layer's audio is off**
(`audioEnabled=false`): the track in the video is always from camera 1, and the insert's soundtrack crept
over the speech. At the entrance and the exit:
- **Quick 2.mov** (`assets → transition`) at `the join − 0.386 c`, blend mode **Add**.
- **Whoosh** (`assets → whoosh`) another `0.083 c` before Quick2.

**The `x`/`y` pan is free, at any scale.** Previously the shift was clamped by the video's slack
beyond the frame: filling by height throws a 16:9 video outside the frame by width by roughly
1160 px on each side — that was the range it moved in, and there is no slack by height at all.
A vertical 9:16 has no slack on either axis, so its `x` and `y` were zeroed and the video
did not move at all. Now `x`/`y` go into the plan and the `.jsx` exactly as
the user set them: having gone beyond the video's edge, the insert reveals the camera frame — both in AE and in
the preview. The slack is computed by `_fill_slack()` in `core/xml2ae/layout.py:431`, and written into the insert's
description by `core/xml2ae/plan_inserts.py:331–332` as the `slackx`/`slacky` fields — this is REFERENCE for the preview,
it does not clip the position; the presence of the fields is checked by `core/verify_jsx.py:400`. The webui preview moves
**the picture inside the frame** through `object-position` (`insVideoPan`,
`static/app/85-inserts-view.js`): previously it moved the whole box together with the frame, and any
X edit looked like a broken edit, although in AE there is no such thing.

**`sin` — from which second OF THE FILE to play the piece** (the "file from" field in the card; from Premiere
it arrives as the source in-point). `vl.startTime = t0 − sin`. The offset is clamped by the file's length
(`sin ≤ duration − window`): beyond the end AE complains about an outPoint outside the source,
and the insert does not end up in the project at all.

## Switching the type photo ↔ video (`insToggleType`)
The insert's type is the **PHOTO/VIDEO** badge on the step 2 card. Switching the type is switching WHAT
to search for: both the stock and the library take the type from the request, and the extension list is set by `insKind` (the same
`IMG_EXT` as in `core/insertlib.py`). That is why the matter does not end with changing `x.type`
(`insToggleType`, `static/app/80-inserts.js`):

- **the variants of the previous type are reset** — `stockOpts`/`libOpts` are zeroed, the panels
  are closed: the previous version changed only the type, and the stock and library variants of the old type
  hung on the card forever (the owner asked precisely "that they re-find the results");
- **open panels are re-searched** and stay open (`insStockFor`/`insLibFor`);
- **an auto-selected file of the previous type is removed** (`x.libAuto` and `insKind(media) != type`), and
  an empty insert is selected anew (`insLibFill`); **a file chosen by hand or
  generated stays** — the generated one has been paid for;
- **the badge changes immediately**, before the network requests (the click does not look stuck), and the flag
  `_typeBusy` prevents a repeated click during the re-search from switching back. The flag is
  live and does not travel into the state (like `genBusy`): a stuck one would survive F5 and would forever swallow
  clicks on the badge;
- **manual output from the library and the stock takes the type from the request** (`insWant`): the priority is soft
  (+0.05 to the score in `match_many`), so a noticeably more suitable video can still
  win over a photo.

## The inserts' timing on step 3 is preserved
Dragging and stretching an insert along the timeline in the step 3 preview used to edit only the PREVIEW
LIST: on the next opening it was rebuilt from the clip cards, and the edit disappeared.
Now the timing (`start_s`/`start_f`) and the duration (`dur_s`/`dur_f`) are written into the clip card too —
by the common card lookup helper, the same one as for the `x`/`y` shift — and are saved
together with the state. Two doors to one state (the preview timeline and the cards) must
converge into one record; a second copy of "where the insert lives" diverges silently.

## The insert card is identified by `uid`, not by the file path
Each step 2 card (`c.inserts[i]`) has a **`uid`** — a string (`crypto.randomUUID()`,
the fallback `Date.now().toString(36)+counter`). It is issued by ONE function
`insEnsureUids(c)` (`static/app/85-inserts-view.js`), and it is called by `ensureJobs` and the opening of the
inserts window: the state arrives from localStorage/the server without a `uid`, and it must appear
exactly once per card. A step 3 insert gets **`cid = card.uid`** in `cardToIns`;
`uid` is saved with the whole clip (`saveState`), and nothing is added to the server.

**Why.** The file path does not distinguish duplicates: three inserts with the same photo — three cards
with the same `media`. The link "step 3 insert ↔ card" was searched for by path and always found
the FIRST one: editing the timing of the second insert (20 → 27 s) moved the first (5 → 27), after the rebuild
the insert "at 5 s" disappeared, and at 27 s there were two of them. The same path also took away the `x`/`y` drag,
the highlight of the playing card and the transfer of manual AE adjustments (`style`/`scale`/`sin`/`noexit`).

**The matching rule** (`insCardFor`):
1. the insert's `cid` → the card with this `uid`; no `cid` — the card itself (its `uid`);
2. the file path — ONLY a fallback key for LEGACY inserts without a `cid`: the first card
   with this path that is NOT YET TAKEN by another insert is taken (not "the first one at all"), and the found link
   is remembered in the insert itself (`x.cid`) — after that the search is exact again;
3. `insCardKey` (stripping the `.nobg.png` suffix from a "on a plate" insert) remains in force for
   the fallback path.

The transfer of `tw` in `ensureJobs` (`static/app/90-ae.js`) works by `cid` in the same way: manual
adjustments go to their own insert and are not copied to duplicates. The highlight of the playing card
(`ipvOverlayPlan`) takes `cid` by the plan's index — the plan itself does not have it from the server, and
`plan.inserts` goes one-to-one along the panel's filtered list of inserts.

## Undo and redo of any insert edits (`insHist*`)
Undo used to be single-level (`INS_UNDO`) and only for deletion. Now per clip (the key is
`xml`) there is a history: a snapshot **`{inserts, ins_rejected, insTarget, job_ins}`** and two stacks —
undo and redo, depth **100**.

- ONE door writes to the history — `insHistTouch(c)`: it compares the serialised current snapshot
  with the last recorded one; if it changed → the previous one goes into the undo stack, the redo stack is cleared.
  It is called by **`saveState`** (`static/app/99-boot.js`) for the clip whose window is open —
  either the step 2 inserts window (`curIns`) or the step 3 preview (`curAE` + an open preview). There is no need
  to enumerate the doors (deletion, addition, file selection, timing, drag, mask, scale, style, rejection):
  any edit reaches `saveState` — and gets into the history.
- The instantaneous flags `genBusy`/`_typeBusy` are not written into the snapshot — as in `stateObj`: otherwise
  every flicker of a button would give a "history step", and an undo could bring back a stuck "…".
- `insUndo`/`insRedo`: `Ctrl+Z`, `Ctrl+Shift+Z`, `Ctrl+Y` — while the inserts window or the
  step 3 preview is open and the focus is not in an input; the "Undo" and "Redo" buttons in the inserts panel's row
  are the same doors. `Ctrl+Y` and `Ctrl+Shift+Z` — redo.
- Applying a snapshot returns the fields to the clip, redraws the preview through the common path (`ipvAfterEdit`)
  and saves the state WITHOUT writing to the history (the `INS_HIST_APPLY` flag) — otherwise the undo itself would become
  a step and there would be nothing to clear the redo with.
- Changing the clip in the window (`openInsertsFor`/`openAEPreview`) resets the history (`insHistReset`):
  the base is the state that was on screen.

## The frame of the "Inserts" window (step 2) and the step 3 preview
The "Inserts" window grew together with the list: with many inserts the timeline went off the bottom of the screen,
and on deletion the window shrank and the buttons changed place. Now:

- the window is **the whole visible screen and one height for any number of inserts**; the list of cards
  scrolls INSIDE itself, while the player, the buttons and the timeline stay in place. The chain of
  containers is height-limited (`min-height: 0`), without it a flex element does not let
  a descendant shrink;
- on a large monitor (1920×1080) the window is 1560×1015, the frame 334×594 — the same numbers as the step 3
  preview; the frame grows together with the column. The previous 720 px gave "a small rectangle with
  a tiny video";
- **the frame's rules apply only to step 2** (`#mbInserts .modal:not(.aemode)`): the frame
  touched the step 3 preview (a hidden column of cards was shown in it, and the words panel
  shrank to the right). The step 3 layout is as before the frame appeared.

The frame's tests are geometry in Chrome: one height at 2 and 30 inserts, no page
scrolling, the timeline inside the window; step 3 is checked against the CSS bench with a tolerance of ±2 px.

## Stock — the order of the providers and why

When there is no suitable file in the library or in generation, the frame is taken from a stock
(`core/stock.py`). The order of the search is **Pexels → Unsplash → Pixabay → Openverse → Coverr**.
It is a priority, not the alphabet, and it was measured on 30 of the owner's queries: Pexels and
Unsplash almost always give 3–4 relevant frames out of 4; Pixabay pulls other tags on hard queries,
and in the old order it filled the gap after Pexels with the weakest frames; Openverse is mostly
junk. A shortfall from Pexels is topped up by Unsplash before Pixabay. A refusal from Unsplash for
its demo limit (429 or 403) does not fail the search: it goes on to the next stock. Coverr (video)
stays behind the flag `REELSI_STOCK_COVERR` until a live key has been checked. The key fields in ⚙
and in `docs/STOCK_PROVIDERS.en.md` are in the same order.

A stock key goes only with the request to its API, never with the download of a file. Search answers
are cached (Pexels and Pixabay allow it, about 24 h), and the file is always downloaded to our own
disk: Pixabay forbids hotlinking. The downloaded file goes into `stock/<stock>/` of the library, next to
`<file>.license.json` with the stock, the id, the author, the link, the licence and the date.
Openverse keeps only the licences that allow commercial use and modification. Unsplash requires the
download to be reported with a request to `links.download_location`.

## Inserts by name — the personal dictionary `named_inserts.json`

A word from a personal dictionary, spoken in the speech, puts its picture from the insert library on
the screen. The dictionary is `named_inserts.json` in the repository root; it is in `.gitignore` and
does not go into git. The example with neutral words is `data/named_inserts.example.json`: copy it to
`named_inserts.json` and fill it with your own words. With no file the mechanism is silently off: no
error and no line in the log for each clip.

The format: the key is the canonical name of the object, the value is the forms it is called by in
speech and in the library; the endings are matched by the word stem. Service keys begin with `_`:

- `_prefer` — words the picture must contain: a library entry without one of them is not used; `коробк*`
  matches by the start of the word, without `*` the word must match whole;
- `_avoid` — words that spoil a picture (a story, a defect); they give a penalty when choosing;
- `secondary` — secondary forms (a brand's older names, a variant spelling): pictures are searched by
  them only when the main names find nothing in the library.

The pass is deterministic over the words of the clip, after the model's answer (`core/aicut/commands.py`):

- a word from the dictionary → a library entry → a photo insert at the moment of the word, 2.5 s long by default;
- the name is not placed closer than 4 s (`NAMED_GAP_SEC`) to an insert already there; one name is used
  at most once in a 20 s window (`NAMED_SAME_SEC`); a stem shorter than 3 characters is not taken from the
  dictionary (`NAMED_STEM_MIN`);
- the choice of the entry (`core/insertlib.py`, `find_named`): a main name beats a secondary one; a name in
  the FILE NAME beats one that is only in the description; fewer `_avoid` words is better; a shorter file
  name is better; then the order in the library. If no entry has a required `_prefer` word, there is no
  insert by name, and the log says "no picture".

Inserts by name are counted in the speaker's photo quota, but the quota does not push them out: if the
photos exceed the quota, the weakest AI inserts go first. The switch is the speaker profile checkbox
"Inserts by name" (key `named_inserts`, on by default). The old key `drug_inserts` is read as a fallback,
and old inserts with `auto=drug` are understood as `auto=named`.

## The background of a photo insert — the cut-out model

A photo insert from a stock, or a generated one, gets its object without the background through rembg when
background cut-out is on (`core/stock.py`, `_strip_bg`). The model is chosen in ⚙ → "Generation" →
"Background removal model" (the key `rembg_model` in `ai_config.json`):

- `u2net` — the default, fast (about 0.7 s per picture);
- `birefnet-general` — cleaner edges of hair and small objects, about 8 s on a processor; on the first use it
  downloads a model of about 1 GB into the rembg folder.

Measured on 12 pictures of the library: BiRefNet is better on 3 (it leaves no piece of background), worse on 1,
and there is less translucent haze in 9 of 12. The cut-out in the cache is marked with its model (`REMBG_TAG`
in `core/insertlib.py`): after a change of model the picture is recomputed, and an old cache without the mark
counts as made by u2net.

## Tuning
All the numbers are in the JSX STYLE block: `INS_ENTER/INS_EXIT/INS_BLUR/INS_POP`,
`INS_C2_PEAK/INS_C2_BASE` (the cam2 zoom = PEAK/BASE of the settled scale),
`INS_C1_UP/DN/LOW/HIGH`, `INS_C1_ON2_X/Y`, `TR_IN/TR_SFX_LEAD`,
the shadow `INS_SH_OP/INS_SH_DIR/INS_SH_DIST/INS_SH_SOFT`, the mask rounding `INS_MASK_R`.
The Quick2/whoosh files — in `assets/assets.json`.
