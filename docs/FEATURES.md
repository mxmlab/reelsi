# Every feature, step by step

Reelsi is a local editing assistant for talking-head video. This document walks through
the three-step wizard and lists every feature it has: what it does, where it lives in the
interface, how to use it, and what it costs.

Russian version: [docs/FEATURES.ru.md](FEATURES.ru.md).

## How it works

The interface is one wizard with three steps: **Cut** → **Markup** → **After Effects**,
plus a settings dialog and a separate Video tab. Everything heavy runs on your machine:
speech recognition, camera sync, the breath detector, rotoscoping and draft rendering.

Only text goes to an API: a text LLM decides what to cut, which words to highlight, where
inserts go and what the intro says. That LLM can also run locally in LM Studio or Ollama.
Image and video generation always go through a provider API.

## Before you start

### Workspace layout

Keep the repository in one folder next to your footage. Reelsi writes results next to the
footage, so the wizard only needs to know where the camera folders are.

```
my_workspace/
├── reelsi/            ← this repository
├── camera1/           ← camera footage
├── camera2/
├── music/             ← downloaded tracks (optional)
└── Reelsi_out/        ← cut results and sidecar files
```

**Where:** step 1 › **Project folder (where cameras are)**
**How:** 1. Put the repository next to the footage. 2. Set the project folder. 3. Press
**Rescan folders** — Reelsi lists the camera folders it found.
**Settings:** **Output folder** is where cut XML files and their sidecar files land. The
`.jsx` folder is set on step 3.
**Code:** `templates/index.html:52`, `api/files.py:30`, `webui.py:81`

### Speaker profiles

A speaker profile is "who is on camera" as a set of settings: their folders, their cutting
thresholds, their default AE style and their hints to the AI.

**Where:** step 1 › **Speaker** (pencil to edit, plus to create), and the same dialog from
a clip row on step 3.
**How:** 1. Press plus next to the **Speaker** selector. 2. Fill in the name, the result
folder, the `.jsx` folder and the render output folder. 3. Pick a default AE style and
write a hint to the AI if the speaker needs one. 4. Save.
**Settings:** cutting thresholds (silence, voice, word padding, hole length and others)
are on the collapsed **Calibration — handled by the assistant** panel; the breath detector has its own cut and mark
thresholds. Extra prompt suffixes for image and video generation live in the same dialog,
and so do the two plate slots — **Plate: add-on to prompt 1/2** — which are used instead of
the ordinary slots by inserts with the **on plate** checkbox.
**Limitations / price:** profiles are plain JSON in `speakers/`, which is gitignored. The
style in a profile is a default — you can always pick another style on step 3.
**Code:** `core/speakers.py:29`, `core/speakers.py:68`, `api/presets.py:85`,
`templates/index.html:570`

### AI provider profiles

A profile is one provider plus one model plus one key. Steps of the wizard pick which
profile works for them.

**Where:** header ⚙ › **Connections**.
**How:** 1. Open ⚙ and pick or create a profile. 2. Choose the provider and the model.
3. Paste the API key and press **Test**. 4. Press **Save**.
**Settings:** the supported providers are LM Studio and Ollama (local), CommandCode,
Anthropic, OpenRouter, DeepSeek, Groq, and any OpenAI-compatible endpoint with your own
Base URL. A key can be written as `env:NAME` and then stays out of `ai_config.json`.
Extra headers are supported for gateways. Model capabilities (reasoning levels,
temperature, caching) come from the models.dev catalog, cached on disk for a day.
**Limitations / price:** the key is stored on the server only and returned to the browser
masked. Text calls cost pennies; images and video are billed by the provider for the model
you pick. Local providers are free.
**Code:** `core/aicut/config.py:41`, `core/aicut/config_actions.py:41`, `core/aicut/catalog.py:1`,
`templates/index.html:890`

### Model catalog and call statistics

**Where:** ⚙ › **Connections** › **AI call diagnostics**; the model list is in the
**Model** field on the same tab.
**How:** 1. Press **Refresh list** to ask the provider for its models. 2. Open the
diagnostics panel to see what previous calls cost.
**Settings:** the panel groups calls by model, step and reasoning level and shows medians
of tokens, reasoning share and time. Failed calls are counted separately.
**Code:** `api/ai.py:245`, `api/ai.py:524`, `core/aicut/config.py:38`,
`core/aicut/catalog.py:22`

### Image and video generation: what is bundled

Local image and video generators are not built into the code. Generation goes to an
image model or a video model through a provider API. The project is open source, so anyone
can plug in a local backend at the extension points `core/aicut/images.py` and
`core/aicut/video.py`.

## Step 1 — Cut

### Downloading footage from Google Drive

**Where:** step 1 › **Get material from Google Drive** (a collapsed block at the top).
**How:** 1. Paste a Drive link into **Link**. 2. Pick the **Download folder**. 3. Press
**Download**. 4. Pick the downloaded camera 1 file and press **Spread by cameras**.
**Settings:** `rclone` must be configured once with `rclone config`; the sign-in happens in
your browser. The form keeps its state between sessions.
**Limitations / price:** downloads go through `rclone`, so large files are fine; progress
is parsed from rclone's statistics.
**Code:** `api/gdrive.py:244`, `core/rclone.py:46`, `templates/index.html:32`

### Project, camera count and the queue

**Where:** step 1 › **Project**.
**How:** 1. Set the project folder. 2. Choose the number of cameras (1 to 4). 3. Pick a
file for every camera. 4. Press **Add to queue** — or **Auto-pair by name**, or **Match by
audio**.
**Settings:** **Match by audio** finds the take of the same scene in the other camera
folders by audio correlation; the threshold was measured on real footage, so "nothing
similar found" means exactly that. **Match all by audio** repeats that for the whole
camera 1 folder, and **new only** skips takes that already have an XML in the output
folder.
**Code:** `templates/index.html:49`, `api/files.py:57`, `core/sync.py:113`,
`core/sync.py:26`

### Audio sync

**Where:** step 1 › **Project** (the **Cameras** switch) — sync runs as part of the cut.
**How:** 1. Choose 2 or more cameras. 2. Queue the pairs. 3. Sound is aligned by
cross-correlation before cutting.
**Settings:** offsets are written to `<stem>.project.json` and can be seen in the camera
layout window.
**Code:** `core/sync.py:82`, `core/sync.py:42`

### AI cutting

**Where:** step 1 › **Cut** › **AI cut** (the primary button).
**How:** 1. Fill the queue. 2. Set the output folder. 3. Press **AI cut**.
**Settings:** the CTC model listens to the whole file and returns every word with its own
timings. Then the text model gets the full transcript and returns the same text with the
parts to drop in square brackets; the code aligns that answer back to the words. Silence
longer than 0.8 s is always cut. The AI model and its reasoning level are set in ⚙ ›
**Cut**. A draft mp4 is built at the end of every cut.
**Limitations / price:** only text goes to the provider. VRAM holds one heavy model at a
time, so GigaAM is unloaded before the LLM is called.
**Code:** `core/gigaam_cut/asr.py:49`, `core/gigaam_cut/decide.py:26`,
`core/gigaam_cut/decide.py:169`, `core/gigaam_cut/pipeline.py:61`

### Custom cutting and its stages

**Where:** step 1 › **Cut** › **Custom**; the stages themselves are in ⚙ ›
**Cut** › **Cutting stages**.
**How:** 1. Press **Custom**. 2. Tick the stages you want in ⚙. 3. Run it.
**Settings:** the stages are one list, shared by the backend and the interface:

| Stage | What it does |
|---|---|
| **Pauses** | Cut pauses: by words (speech), by sound energy (loud) or off |
| **Speech recognition** | Word timings from ASR; switched on automatically when another stage needs it |
| **Meaning (AI)** | The LLM marks semantic chunks and drops failed retakes |
| **Cut editing by code** | Code on top of the AI answer: repeats, restarts, micro islands; helps weak models and hurts smart ones |
| **Cut refinement** | Trim cut points to the sound envelope and word boundaries |
| **Breaths** | Detect and cut breaths before phrases |
| **Draft mp4** | Render a quick `.draft.mp4` for previewing the cut |

Choosing **Pauses → by volume** switches to the legacy branch: VAD plus Whisper instead
of CTC word timings. The VAD thresholds (**Silence threshold (dB)**, **Min pause (s)**,
**Padding (s)**) appear in the same settings tab.
**Limitations / price:** the **Draft mp4** stage has no checkbox of its own; it is switched
on together with **Omni review**.
**Code:** `core/cutstages.py:16`, `core/cutstages.py:87`, `core/cutstages.py:149`,
`templates/index.html:801`, `static/app/10-settings.js:243`

### Breath detector and the disputed-breath strip

**Where:** step 1 › **Cut** (runs during the cut), and the strip in the cut editor.
**How:** 1. Run a cut. 2. Open the editor. 3. A click on the orange strip cuts that
breath out.
**Settings:** a speaker profile can set its own cut and mark thresholds; the defaults are
0.95 to cut and 0.50 to mark. A segment whose speech probability is above 0.25 is
never cut automatically. Retrain the model with `python tools/train_breath.py`.
**Limitations / price:** three sources vote: Silero VAD, CED-tiny in isolation, and
acoustics. If `silero-vad`, `transformers` or the model file is missing, the detector
switches itself off and cutting proceeds as before.
**Code:** `core/breath.py:40`, `core/breath.py:229`, `core/breath.py:254`,
`api/editor.py:135`

### Cut editor

**Where:** step 1 › clips list › **Edit** (the preview window, the left half).
**How:** 1. Open a clip. 2. Click to place the cursor, drag block edges, double-click on
grey to bring cut material back. 3. Press **Save to XML**.
**Settings:** wheel zooms, Shift+wheel or the ruler scrolls, Space plays, arrows step
frame by frame. **Cut** (C), **Delete** (D) and **Undo** (Ctrl+Z) work on blocks. The
**listen to the cut** checkbox plays the removed audio as well.
**Limitations / price:** saving rewrites the XML and reprojects insert timings onto the new
edit; the window warns before closing with unsaved changes.
**Code:** `templates/index.html:306`, `static/app/70-editor.js:13`,
`api/editor.py:256`

### Camera layout

**Where:** step 1 (a clip row) › the camera layout window.
**How:** 1. Open the layout window for a clip. 2. Press a row to jump to that piece.
3. Swap the camera for a piece. 4. Press **Save layout**.
**Settings:** the legend and the strip show which camera is on screen for every piece.
**Reset to auto** returns to the automatic layout. The player below plays the edit and
**Audio** lets you listen to another camera to check sync.
**Limitations / price:** with two cameras the layout alternates strictly and both the first
and the last piece stay on camera 1. Do the layout before markup: rebuilding the XML for it
erases subtitles.
**Code:** `core/align.py:14`, `api/build.py:340`, `templates/index.html:458`

### Draft mp4

**Where:** step 1 › **Cut** — the draft is built at the end of each AI cut; the file
lies next to the XML.
**How:** 1. Run an AI cut. 2. Press **Edit the finished ones** in the progress overlay
while the queue continues. 3. Or run a custom cut with the **Draft mp4** stage.
**Settings:** draft rendering uses 720p camera proxies cached in `_tmp`; the proxy is
built once per camera file.
**Limitations / price:** NVENC is used when available, with a fallback to CPU x264, which
is slow; the log says so explicitly.
**Code:** `core/draftrender.py:1`, `core/cutstages.py:71`, `api/jobs.py:504`,
`static/app/50-chrome.js:186`

### Temporary files

**Where:** ⚙ › **Tools** › **Temporary files** › **Clear**.
**How:** 1. Open the dialog. 2. Confirm. 3. If preview proxies exist, answer the second
question separately.
**Settings:** the dialog shows the size before deleting. Drafts and the roto cache are left
alone; preview proxies are a per-camera-file cache and are only removed if you say yes.
**Code:** `api/jobs.py:550`, `api/jobs.py:593`, `static/app/50-chrome.js:194`

### Clips list

**Where:** step 1 › **Clips**.
**How:** 1. Use **From output folder** to pick up every `.xml` in the output folder.
2. Use **Add XML…** to bring in a timeline edited elsewhere. 3. The broom clears the list.
4. Tick clips and press the trash button to delete them.
**Settings:** every clip carries a selection checkbox — the same one as in the step 2 and
step 3 lists, because the choice is shared by all three. A click with Shift sets or clears
the whole range from the last clicked checkbox to this one. The keyboard focus stays on the
checkbox: all three lists are redrawn on every pick, so the checkbox a person was working
with is put back into its own list afterwards — Tab, Space and a Shift range taken from the
keyboard go on walking the list in order; a focus that stood elsewhere is not moved. The trash
button **Delete the selected clips** in the list header (disabled while nothing is ticked) opens
the same dialog as the cross on a clip row: **Remove from the list** or **Delete from disk…**; the second one
shows the combined list of cut files with their sizes and then erases them together with
every sidecar. The source camera video is never touched, and one failing clip does not stop
the others. The list lives in browser state and in a server mirror, so it survives a reload
and a browser change.
**Limitations / price:** nothing ticked here does NOT mean "all", unlike the build: there is
nothing to delete, so the button stays disabled.
**Code:** `templates/index.html:112`, `static/app/40-queue.js:592`,
`static/app/40-queue.js:720`, `api/files.py:534`

## Step 2 — Markup and inserts

### AI markup

**Where:** step 2 › **Markup**, with the per-task buttons **Subtitles**, **Highlights**,
**Inserts** and the primary **Mark up all**.
**How:** 1. Tick the clips you want (nothing ticked means all of them). 2. Press a phase
button to run just that phase. 3. Or press **Mark up all** to run subtitles, then
highlights, then inserts.
**Settings:** which model works on which task is set in ⚙ › **Markup**: a separate model
and reasoning level for highlights, inserts and intro, plus the subtitle engine. The checkbox
column here is the same shared selection as on steps 1 and 3 — a click with Shift takes a
whole range — and the trash button in the list header deletes the ticked clips.
**Limitations / price:** clips without subtitles are skipped by the highlight and insert
phases. Inserts target 10 photos and 3 videos per video, from 6 s and 10 s in.
**Code:** `templates/index.html:122`, `static/app/70-editor.js:296`,
`api/ai.py:32`, `api/ai.py:61`

### Subtitles

**Where:** step 2 (during markup) and the inserts window › **Subs** tab.
**How:** 1. Run the subtitle phase. 2. Check the rows in the **Subs** tab. 3. Click a row
to jump the preview to it.
**Settings:** the engine is chosen in ⚙ › **Markup** › **Subtitle engine**. The default is
Whisper large-v3; GigaAM heads are also offered: CTC is pure acoustics and fast, RNN-T
corrects words with its own language model, e2e RNN-T adds punctuation and casing, and the
multilingual model covers 70+ languages. Words per row and the maximum number of rows are
style keys with fields in the same tab.
**Limitations / price:** words longer than the reference blobs allow used to disappear from
the XML; the template now grows for them, and anything still skipped is reported in the
UI log. `.srt` files are written next to the XML.
**Code:** `core/asr_backends.py:44`, `api/editor.py:398`, `core/align.py:355`,
`static/app/70-editor.js:323`

### Word highlights

**Where:** step 2 (markup), then the clip preview › **Words** panel.
**How:** 1. Run the highlight phase. 2. Click a word to highlight it — another click
clears it. 3. Double-click sends a word to the intro. 4. Ctrl+click edits the word text
straight in the XML; clearing the text deletes the word.
**Settings:** the highlight colour and bold are style keys; the third colour is used by
intro rows marked as accents. From the keyboard: Enter highlights, Shift+Enter sends to
intro, Ctrl+Enter edits. A deleted word gives its time to the next one when the two went
back to back (a pause of 0.3 s or less): the next word starts where the deleted one started
and lasts longer. Gaps left by earlier deletions do not close by themselves.
**Limitations / price:** highlights are written into the XML itself, so they survive a
manual re-edit; a sidecar `.yellow.json` is kept as a fallback for words that could not be
coloured.
**Code:** `core/aicut/commands.py:50`, `static/app/60-preview.js:469`,
`api/editor.py:510`, `api/editor.py:583`

### Inserts editor

**Where:** step 2, and the clip preview › **Inserts** and then **Insert editor (AI)…**.
**How:** 1. Press **Re-pick (all)** or **Fill in the missing**. 2. Drag a card block
on the timeline to move it, drag its edges to change the duration. 3. Add a photo or a
video by hand, or remove a card. 4. Press the undo icon to bring the last removed card
back.
**Settings:** **photo** and **video** buttons add cards manually; the placeholder in the
preview marks where a new card will land. A video insert plays its whole length and is
never trimmed; it is not rebuilt on every edit, so it does not blink black while a field
is being changed. The **scale** scrubber works on every video insert and grows it from the
centre of the frame, the way layer Scale does in After Effects; **X** and **Y** move a
video insert freely at any scale, in the preview and in AE alike — a video that went past
the edge of the frame uncovers the camera shot under it.

**On a plate.** The **on plate** checkbox next to the mosaic one puts that single photo
insert on the plate image from the style (**Inserts** › **Photo** › **Plate (file)**,
**Plate scale, %**). The plate is drawn under the photo, the rounding mask is not applied to
such an insert, the photo background is removed with `rembg` and cached next to the file as
`<name>.nobg.png` (so only the first build is slower), and the generation prompt comes from
the speaker's **Plate: add-on to prompt 1/2** slots. The scale and position fields of such
an insert move only the photo inside the plate; dragging the insert in the step 3 preview
moves the whole card — plate and photo together. Inserts without the checkbox are untouched:
ordinary prompt, ordinary mask and effects.

**Limitations / price:** the pick respects forbidden zones: nothing in the first seconds,
nothing in the closing seconds, a minimum gap between inserts and a minimum duration.
**Code:** `templates/index.html:355`, `core/aicut/commands.py:149`,
`core/aicut/commands.py:174`, `static/app/80-inserts.js:339`,
`static/app/85-inserts-view.js:1983`, `core/insertlib.py:956`

### Generating images and video for inserts

**Where:** the insert cards in the insert editor; also ⚙ › **Generation** for the profiles.
**How:** 1. Pick an image profile in ⚙ › **Generation**. 2. Press button 1 or 2 on a card
to generate an image for its query with that prompt slot. 3. Press **Missing only** to
fill every photo card that has no file. 4. For video, use the same numbered buttons on a
video card, or the **Video** tab.
**Settings:** the image profile needs an image model; the video profile needs a provider
and a video model. **Remove background** in the settings cuts the subject out of every
generated image with `rembg` and saves a transparent PNG; **Remove background** does the same
for one file on a card. The speaker profile can add a suffix or prefix to the prompt for
both image and video slots — that is what the numbers 1 and 2 choose.
**Limitations / price:** this is the paid part: the provider bills per image and per video.
The engine refuses to run without an image or video model, and generation is cancellable.
Generated media is added to the insert library and is reused for free later.
**Code:** `core/aicut/images.py:45`, `core/aicut/images.py:142`, `core/aicut/video.py:34`,
`api/ai.py:374`, `api/ai.py:435`

### Video tab

**Where:** the **Video** tab, reachable from ⚙ › **Tools** › **Video generation** ›
**Open**.
**How:** 1. Describe the shot. 2. Set duration, resolution, aspect, seed and audio.
3. Press **Generate**. 4. Watch progress and press **Stop** if needed.
**Settings:** length, resolution and aspect are computed from the checked video reference
and the model capabilities, and shown read-only. References can be attached as HTTPS
links with a caption; the first and last frame roles accept photos only, and style
references are supported by Seedance 2.0 with up to 3 clips and 15 s in total.
**Limitations / price:** generation runs in the cloud and takes minutes; the task list
survives a page reload, and **Retry** puts a task's prompt and settings back into the
form. Removing a task removes its file too.
**Code:** `templates/index.html:240`, `core/aicut/video.py:16`,
`api/videogen.py:218`, `api/videogen.py:348`

### Insert library

**Where:** the insert editor › **Library**, and the clip row in the inserts window.
**How:** 1. Press **Scan** to index past projects and media folders. 2. Search by name or
description. 3. Press **Describe with AI** to have a vision model write descriptions for
files that have none. 4. Use **Import** to move downloaded media into your library folder.
**Settings:** scanned folders are typed one per line. **auto-pick after AI** makes
generation pick a library file when it can. Descriptions are just text — edit them in
place and matching follows.
**Limitations / price:** matching is semantic through LM Studio embeddings, with a token
fallback when no embedder is available. Service files are skipped: transitions, sounds,
roto masks, drafts and camera sources.
**Code:** `core/insertlib.py:1`, `api/inserts.py:89`, `api/inserts.py:208`,
`api/inserts.py:177`, `templates/index.html:494`

### Censoring

**Where:** ⚙ › **Words** › **Censoring**; the audio switch is in the same tab on step 3.
**How:** 1. Type bad stems, one per line. 2. Add ordinary words that merely contain a bad
substring to **Exceptions**. 3. Tick **Censor audio** on step 3 if the voice should dip
as well.
**Settings:** matching is by substring, so the stem `убива` covers its forms. Lines
starting with `#` are comments. Your edit goes to `badwords.user.txt` / `okwords.user.txt`
and replaces the shipped list; **Default** brings the shipped one back.
**Limitations / price:** a censored word is written into subtitles with a star instead of
its middle letter, and the audio is muted on those frames only if that switch is on. Lists
are reloaded by file modification time, so no restart is needed.
**Code:** `core/censor.py:88`, `core/censor.py:98`, `core/xml2ae/layout.py:825`,
`api/presets.py:171`, `templates/index.html:866`, `templates/index.html:220`

### Glossary of terms

**Where:** ⚙ › **Words** › **Glossary**.
**How:** 1. Type names one per line, exactly as they should appear in subtitles. 2. Fix a
misheard word in the words panel to the real term and the program remembers that variant.
**Settings:** two deterministic stages: exact variants of a normalised word chain (the
only way Latin text and digits are caught), and fuzzy matching with similarity above 0.82
for words of at least 6 letters. Remembered variants appear as chips under the name.
**Limitations / price:** the list starts empty on purpose — a wrong term is worse than no
term. Terms are applied inside speech recognition, so every engine benefits and the
timings are not touched; the self-check disables them, because it compares words one to
one.
**Code:** `core/terms.py:203`, `core/terms.py:268`, `core/asr_backends.py:116`,
`api/presets.py:137`

### Video caption

**Where:** the clip preview › the **Video caption** field above the words panel.
**How:** 1. Type the video caption. 2. Press **Save**.
**Settings:** the video caption is a style block with its own font, size, casing, colours and a
background plate.
**Code:** `templates/index.html:426`, `api/editor.py:47`, `static/app/80-inserts.js:198`

## Step 3 — After Effects

### Set files and clip selection

**Where:** step 3 › **Set files**.
**How:** 1. Tick the clips that go into the build. 2. Nothing ticked means the whole set.
3. Click a row to open the preview for that clip.
**Settings:** the selection is the same as on steps 1 and 2 (a click with Shift takes the
whole range), and the trash button **Delete the selected clips** next to the broom removes
the ticked clips from the list or erases their files from disk. The broom removes ticked
clips from the list only; files on disk stay.
**Code:** `templates/index.html:151`, `static/app/90-ae.js:280`,
`static/app/40-queue.js:640`

### AI intro: hook and accents

**Where:** step 3 › the clip preview › **Intro** bar, or **AI intro (n)** for the whole set.
**How:** 1. Open a clip preview. 2. Press **AI intro**. 3. Check the rows: the first rows
are the hook behind the speaker, the mid rows are accents.
**Settings:** the intro appearance mode is **per word** or **per line**. Rows can
be added and reordered; picking a row and clicking a word moves the group start.
**Limitations / price:** already marked-up intro is replaced after a confirmation. Intro
words are cut out of the subtitles, so they do not show up in the subtitle rows either; this
works the same in the row mode and in the word-by-word mode.
**Code:** `api/ai.py:463`, `static/app/90-ae.js:283`, `static/app/90-ae.js:392`,
`core/aicut/commands.py:1`

### Editing word highlights

**Where:** the clip preview › **Words** panel (the same panel as on step 2).
**How:** 1. Click a word for a highlight. 2. Double-click for an intro accent. 3. Ctrl+
click to fix the text — an emptied field deletes the word, and the deleted word gives its
time to the next one when they went back to back (see **Word highlights** above).
**Settings:** a `|` typed between two words breaks the stack. A word that already went to
the intro is edited in its group row above.
**Code:** `static/app/60-preview.js:442`, `static/app/60-preview.js:469`,
`api/editor.py:583`

### Styles

**Where:** step 3 › **Setting: <clip>** › the style block, split into the tabs **Text**,
**Frame**, **Inserts**, **Layers** and **Sound**.
**How:** 1. Pick a style in the selector. 2. Edit the values you need. 3. Press **Save**,
or **Save as…** for a copy.
**Settings:** **Text** holds subtitles (words per row, rows, height, subtitle scale,
casing, colours), highlights (including **Yellow in a row**, the blur-in and **consecutive
yellow — stacked**), the subtitle
plate, the caption, intro colours, glow and shadows, the disclaimer, and the fonts.
**Frame** holds the camera 1 zoom mode
(push-in with recoil, hard jumps, drift, none), the take zooms and the yellow-word zoom,
frame fill, the zoom point, the frame offset, the horizon, head tracking, the Lumetri
colour, the top progress line and the start blur.
**Inserts** holds the photo style, animation, effects, insert positions, the plate image and
the continuous rotoscope. **Layers** is the layer order, dragged with the mouse or moved
with the arrow buttons. **Sound** holds music, voice, transition, glitch and pop levels,
their files and their hit points.
**Limitations / price:** editing a style's own template and saving it applies the change to
every file in the set that uses that style. Built-in styles cannot be deleted.
**Code:** `core/styles.py:1`, `api/presets.py:15`, `templates/index.html:170`,
`static/app/95-styles.js:496`

### Camera 1: hard jumps, take zooms and yellow words

**Where:** step 3 › style › **Frame** › **Cam 1 zoom**.
**How:** 1. Pick the mode: **push-in with recoil (smooth)**, **hard jumps 100–140%**,
**drift 100–160% (smooth between cuts)** or **no zoom (static frame)**. 2. In the **hard
jumps** mode set **punch-in at start**, **First punch-in, %**, the take zooms and the
yellow-word zoom.
**Settings:** in **hard jumps** the scale jumps to a random value between **Pullbacks from, %**
and **Pullbacks up to, %** at every cut. **punch-in at start** opens the clip with a smooth approach from **First
punch-in, %** down to the first jump instead of starting on a random value. **zoom-ins on
long takes** adds one smooth approach inside every take longer than **Take longer than, s**:
the camera moves in by a value between **Zoom-in from, %** and **Zoom-in to, %** of that
take's own value, holds it for **Hold
zoom-in, s** and pulls back; if the next cut comes too soon it stays zoomed in and the cut
resets it with a jump. **zoom-in on yellow words** lands that approach exactly on the first
yellow word of the take instead of a fixed moment after the cut; a take without yellow words
behaves as usual.
**Limitations / price:** the take zooms and the yellow-word zoom work only in **hard
jumps**; in the other modes their fields are hidden.
**Code:** `core/xml2ae/plan_camera.py:118`, `core/style_schema.py:1331`, `core/styles.py:184`

### Camera 1 frame: fill, zoom point, offset and horizon

**Where:** step 3 › style › **Frame** › **Transform**.
**How:** 1. Set **Frame fill, %**: 100 fills the frame exactly, 120 pushes in by 20%.
2. Type the zoom point in percent of the frame (X and Y), or press the crosshair button and
click the frame in the preview. 3. Use **Frame offset X, px** and **Frame offset Y, px** to
move the whole frame and **Horizon, °** to tilt it.
**Settings:** the zoom point is what the zoom is measured from: it stays put while the
camera moves in. Both of its numbers are dragged with the mouse like any other number field
(Shift takes a ten times bigger step). **Frame fill, %** is a common multiplier of the
camera 1 zoom: like the null's Scale it grows the frame together with the camera 1 inserts
and the intro, and the zoom point stays put. **Frame offset X, px** and **Frame offset Y, px**
move the whole frame (the null's Position), so camera 1 inserts and the intro travel with it,
while the zoom point does not move. **Horizon** turns only the camera 1 picture and its rotoscope; inserts and
the intro stay straight, so at a zoom near 100 % the corners open up — keep some zoom in
reserve.
**Limitations / price:** the horizon field goes to ±10° (±45° in the extended range) and the
frame offset to ±500 px (±2000 px in the extended range).
**Code:** `core/xml2ae/plan_camera.py:143`, `core/style_schema.py:1234`,
`static/app/94-stylepanel.js:1314`

### Head tracking

**Where:** step 3 › style › **Frame** › **Transform** › **follow the head**.
**How:** 1. Tick **follow the head**. 2. Set **Head X, %** — where the head should sit in
the frame. 3. Set **Follow smoothing, s** and, if needed, **Follow from zoom, %**.
4. Build the set.
**Settings:** the frame moves horizontally to keep the head at the chosen place. The head is
found by the person mask — the same Robust Video Matting that rotoscope uses — and not by
face detection, so a portrait on the wall does not confuse it. The track is computed on the
GPU during the first build of a clip and cached next to the XML as `<name>.head.json`; later
builds just read the cache. **Follow smoothing, s** is the time constant of the movement.
**Follow from zoom, %** switches the tracking on only from that zoom value: below it the
frame smoothly returns to its place, 0 means always follow. The threshold is compared in the
same numbers as the jump and take ranges, without the frame fill. Camera 1 inserts and the
intro travel with the frame.
**Limitations / price:** the correction is limited by the frame itself: the edge of the
picture never opens. Tracking needs the GPU and the matting model (downloaded on first use,
as for rotoscope); if it fails, the build continues without tracking and says so in the log.
**Code:** `core/headtrack.py:201`, `core/xml2ae/layout.py:1156`,
`core/xml2ae/build.py:1756`

### Colour (Lumetri)

**Where:** step 3 › style › **Frame** › **Color (Lumetri)** (the group has its own
checkbox).
**How:** 1. Tick the group. 2. Set the nine parameters — **Exposure**, **Contrast**,
**Highlights**, **Shadows**, **Whites**, **Blacks**, **Temperature**, **Tint** and
**Saturation**. 3. Build the set.
**Settings:** one Lumetri Color effect goes on every camera clip and on its rotoscope copy,
with the same nine values as the Lumetri panel in After Effects. The per-clip exposure of
the AE step is added to **Exposure**, so the two do not fight.
**Limitations / price:** the browser preview shows an approximation — the real Lumetri
formulas are closed — so it is good for judging the direction of the correction, not its
exact value.
**Code:** `core/xml2ae/build.py:414`, `core/style_schema.py:1499`,
`static/app/85-inserts-view.js:361`

### Yellow highlights in rows: animation and blur-in

**Where:** step 3 › style › **Text** › highlights.
**How:** 1. Set **Yellow in a row**: **when spoken** or **with the row**. 2. Tick **blur-in**
and set **Blur amount** if the yellow word should come out of a blur. 3. Tick **consecutive
yellow — stacked** if a run of yellow words should leave the rows.
**Settings:** the first choice matters only when a row holds more than one word. With **when
spoken** the white words appear with the row and the yellow one rises at the moment it is
said — until then its place in the row is empty. With **with the row** the yellow word rises
together with the row. **blur-in** adds a Gaussian Blur on the yellow word on the same
keyframes as the rise; the default **Blur amount** is 70.4. **consecutive yellow — stacked**
(the `hl_row_stack` key, off by default) takes a run of two or more yellow words in a row out
of the rows and stacks them one word at a time, exactly as in the word-by-word mode — the
same rise, the same stack step, one common end for the run; a lone yellow word stays in its
row. The entrance itself — the rise, the fade-in and the blur — lasts min(0.35 s, 60 % of the
time the word is visible), so a short word finishes its animation instead of going out in the
middle of it; this holds in the one-word mode, for a yellow word inside a multi-word row,
for a run of consecutive yellows stacked out of the rows and for joined words alike, and the
preview shows the same numbers.
**Limitations / price:** the animation is visible in the browser preview too: the moment a
yellow word appears, its rise, its fade-in and its blur all come from the scene plan — the
same numbers that go into the `.jsx`. With the stack checkbox off the `.jsx` is byte-for-byte
what it was before.
**Code:** `core/xml2ae/layout.py:143`, `core/xml2ae/layout.py:147`,
`core/xml2ae/plan_subs.py:402`, `core/xml2ae/template.py:254`,
`static/app/85-inserts-view.js:838`, `core/style_schema.py:162`

### Intro: several words in a row, camera link, line spacing

**Where:** step 3 › style › **Text** › **Intro**.
**How:** 1. Set **Line spacing, %** (100 is the usual distance). 2. In **Text › Back plane**
set **Background line spacing above, %** and **Background line spacing below, %** — the step
to a background line and back from it. 3. In the intro rows (clip preview › **Intro**) tick
**Big on the left** if a row should stand large on the left of its group. 4. Clear **intro
moves with camera** if the intro should stay in place while the camera moves.
**Settings:** the intro works the same whether the rows mode (**words per row**) is on or
off: its words are cut out of the subtitles, and its font size is the one the subtitles
would have had without the auto-shrink of long rows, so in the rows mode the intro does not
come out smaller. The fields sit in three groups: **Transform** holds the shared **Intro
scale, %**, **Line spacing, %**, the big-word fields, **Intro horizontal position** and the
**intro moves with camera** checkbox; **Camera 1** holds **Intro vertical position** and
**Intro anchor, camera 1**; **Camera 2** holds **Intro on cam2 Y** and **Intro anchor,
camera 2**. **intro moves with camera** keeps the intro on the camera 1 null, so it inherits
the zoom, the frame offset and head tracking; cleared, the intro and the shade under it stand
still in the frame and the group is fitted to **Intro width, %** of the frame (`intro_fit_w`,
92) — grown and shrunk alike, while the attached one is only shrunk; a group whose scale was
set by hand is left alone either way. The growth of a detached group is capped by **Intro max
scale, %** (`intro_fit_max`, 250): without the cap one short word blew up to 667–819 % of the
frame, while shrinking is not limited. Both fields are shown only for the detached intro — an
attached group takes its width from the camera zoom. After the fit the group is lowered by its
actual top, the big word included, and never rises above the safe line of the frame
(`INTRO_SAFE_TOP`, 285 px of 1920): before the fix the lowering was counted before the fit, so
the top of a large detached group climbed as high as 164 px where the line is 285.
**Line spacing, %** multiplies the distance between the intro rows.
**Background line spacing above, %** (the `back_step` key, 10 to 300 %, 65 by default) is the
step to a background line and between background lines; **Background line spacing below, %**
(`back_step_after`) is the step from a background line to the regular line under it — both as
a share of that same line spacing. There are two numbers because the visible gaps depend on
the words (lowercase letters, descenders), and one number cannot make them equal; a style
without the new key uses the "above" value, so saved styles look the same. The old
**Background step** with its glyph-based minimum and the small-row gap are gone; a `back_gap`
left in a saved style is dropped when the style loads.

**Fading out to a subtitle.** An intro group that stands at the subtitle level (by vertical
position) and would outlive the next subtitle fades out exactly to it: over **Quick fade
before subtitles, s** (`intro_sub_fade`, 0.15). The rule is on by default and is turned off
by **Intro fades out to the subtitle** (`intro_sub_cut`). The last group of a clip is not
touched — it holds to the end as before. An appearance that cannot finish before the fade
starts is compressed, but not shorter than 0.1 s. **Intro fade-out, s** (`intro_fade`) is
about something else: it moves the start of the fade, not the moment the group disappears.

**Big on the left.** That checkbox in an intro row puts the row — one word or several — on
the left in a large size, and the other rows of the group stack to its right, left-aligned.
The big word stands on the baseline of the last stacked row, and its height is measured from
the stack: from the cap height of the first stacked row to that baseline, times **Big word
above stack, %** (`intro_big_over`, 110 by default; at 100 the top of the big word is level
with the top of the stack). Letter tails (Ц, Д) hang below the baseline, as in typography.
Appearing does not change its size: the animation grows the word from the layer's base, so a
big word stays big.
**Gap to big word, px** (`intro_big_gap`, 40) is the distance between the big word and the
stack; **Stack line spacing, %** (`intro_big_step`, 80) is the step of the stacked rows and
does not depend on the general line spacing. The layout is computed once, and the preview and
After Effects take the same numbers. If several rows of a group are ticked, the big one is
the first of them; a group of one row has no big word. A clip without such a row builds
byte-for-byte as before.
**Code:** `core/xml2ae/layout.py:322`, `core/xml2ae/layout.py:166`,
`core/styles.py:232`, `core/style_schema.py:479`, `core/style_schema.py:577`,
`static/app/94-stylepanel.js:1514`

### Glitch glow: built-in or Deep Glow 2

**Where:** Settings (⚙) › **Tools** › the **After Effects** block › **Glitch glow**.
**How:** 1. Open ⚙ › **Tools**. 2. Under **After Effects**, choose between **Built-in (Blur + Glow)** and **Deep Glow 2 (plugin)**. 3. Rebuild `.jsx` scripts for clips if already exported.
**Settings:** controls how yellow intro words with the "glitch" animation glow in the generated After Effects project. Built-in uses Gaussian Blur and Glow available in every AE install (default). Deep Glow 2 replaces them with the third-party plugin using pre-tuned parameters; accent lines and other lines remain untouched. In this mode the plugin is not put on a line that has the line glow of its own (tick **Deep Glow with line glow**, `intro_dg_with_glow`, off by default, to get the old behaviour back) and not on a bright highlight colour — the same Rec.709 brightness threshold (above 0.7) as for Tritone, so such a word keeps the built-in Blur + Glow. Saved under the `glitch_glow` key in `ai_config.json` (`builtin` or `deepglow2`) and takes effect on the next build; already built `.jsx` scripts need to be rebuilt.
**Limitations / price:** if Deep Glow 2 is selected but the plugin is not installed in After Effects, manual build shows a single dialog per file reporting the number of unstyled words, while headless rendering writes an error line to the log; words remain without glow. There is no automated pre-flight check or fallback to built-in effects.
**Code:** `core/aicut/config.py:509`, `core/aicut/config_actions.py:219`, `core/xml2ae/build.py:663`, `core/xml2ae/build.py:1866`, `templates/index.html:783`, `static/app/10-settings.js:732`

### Subtitle scale

**Where:** step 3 › style › **Text** › **Subtitles** › **Subtitle scale, %**.
**How:** 1. Move the slider. 2. The whole subtitle precomp grows or shrinks. 3. The layout
itself is untouched: line wrapping, auto-shrinking and the stack step stay as they are.
**Settings:** the default is 100 %, the range is 20 to 200 %. It is a style key, so it is
stored in the style and arrives in AE without a manual Scale.
**Code:** `core/style_schema.py:223`, `core/styles.py:329`

### Attaching a style to a speaker

**Where:** step 1 › **Speaker** selector and the speaker profile dialog; the clip row on
step 3.
**How:** 1. Pick a speaker. 2. Their style is selected automatically. 3. Change the style
on step 3 if you want a different one.
**Settings:** one clip with several speakers uses the style of the first one; the build
button warns about that.
**Limitations / price:** the profile style is a default, not a binding: changing the style
on step 3 does not write back into the profile.
**Code:** `core/speakers.py:32`, `static/app/95-styles.js:228`,
`static/app/90-ae.js:114`

### Layer order

**Where:** step 3 › style › **Layers** tab.
**How:** 1. Drag a row, or press the up and down arrow buttons. 2. The higher row covers
the lower ones.
**Settings:** the layers are subtitles, video inserts, rotoscope, photo inserts and intro.
The default order is subtitles, video, roto, photo, intro.
**Code:** `static/app/95-styles.js:493`, `static/app/95-styles.js:496`,
`static/app/95-styles.js:502`

### Photo inserts: animation, effects and position

**Where:** step 3 › style › **Inserts** › **Photo**.
**How:** 1. Pick the photo insert style: auto, camera 1 (from behind the shoulder) or
camera 2 (push-in with blur). 2. Pick the animation: push-in with blur, rise from below
with a fade, or none. 3. Pick the effect preset: the new one (black shadow plus rounded
mask), the old one (white shadow plus choker) or none. 4. Adjust the resting position per
camera in percent of frame.
**Settings:** camera 1 inserts inherit the camera 1 zoom, so their offset scales with it.
When the **Camera 1** style lands on a camera 2 piece, a separate offset applies.
**Plate (file)** and **Plate scale, %** set the plate image and its size for the inserts
that carry the **on plate** checkbox (see the inserts editor above).
**Limitations / price:** while a rising insert is on screen the subtitles step aside. A
photo that crosses a camera change is trimmed exactly at the change; video inserts are
never trimmed.
**Code:** `core/style_schema.py:1598`, `core/style_schema.py:1649`,
`static/app/85-inserts-view.js:1248`

### Rotoscope

**Where:** step 3 › style › **Inserts** › **Rotoscope**.
**How:** 1. Tick **Auto rotoscope**. 2. Choose the device and the mask bottom. 3. Build the
set.
**Settings:** the mask is computed by Robust Video Matting on the GPU; the mask bottom
percentage cuts the mask, and **Roto on Camera 1 only** skips camera 2 pieces.
**Limitations / price:** the model is downloaded on first use, and masks are cached, so a
second build reuses them. On a VRAM shortage the build stops with a clear message and the
XML is not overwritten. Add the photo and intro layers below roto to bring the person in
front of them.
**Code:** `core/roto.py:1`, `core/roto.py:41`, `core/style_schema.py:1768`

### Music

**Where:** step 3 › **Folders and parameters** › **More — brightness, music, censoring, folders** › **Music (−20 dB)**.
**How:** 1. Choose **random from downloads**, **YouTube link** or **file**. 2. For a link,
paste the URL. 3. For a file, press **Browse…**.
**Settings:** the music folder for downloads is set next to it. The level lives in the
style's **Sound** tab.
**Limitations / price:** a YouTube link requires `yt-dlp`; a random track picks one file
from the music folder at build time.
**Code:** `templates/index.html:223`, `core/ytmusic.py:10`, `core/ytmusic.py:34`,
`api/files.py:471`

### Scene preview in the browser

**Where:** step 3 › **Preview — intro, highlights, inserts**; the same player opens a clip
from step 1, and there the window is titled with the name of the open file.
**How:** 1. Open the preview. 2. Watch the edit with inserts, intro, subtitles and sound.
3. Drag an insert block on the timeline to move it, drag its edges to change the duration.
4. Drag the zoom target point.
**Settings:** the preview uses the same scene plan that goes into the build — the plan is
served by `/api/scene` without GPU work, and every style edit comes back as a new plan, so
the subtitles change at once, on a paused player too. The subtitles do not drag with the
mouse: their height is the style's **Subtitle height, % from bottom**. The style block moves
into the preview's **Style** tab while it is open, and returns to the page when it closes;
there the panel scrolls, so a long list of groups does not push the **Save** row out.
Preview volume is shared by all players, and the music and voice levels in the preview are
the same numbers that end up in the `.jsx`. Preview video and sound are served in 4 MB
pieces, so the browser's limit of six connections per server is not taken up and opening the
preview or saving does not wait for seconds.
**Limitations / price:** camera proxies are built once per camera file to keep seeking
snappy; until a proxy is ready the preview plays the original, and a block with a progress
bar and a percent sits over the player while the build runs. Fonts for the preview come
from the installed system fonts.
**Code:** `api/build.py:639`, `static/app/85-inserts-view.js:55`,
`api/previewproxy.py:66`, `api/files.py:261`, `static/app/50-chrome.js:328`

### Exporting `.jsx`

**Where:** step 3 › the sticky build bar.
**How:** 1. Tick the clips. 2. Choose **Separate .jsx** or **One for all**. 3. Press
**Build set**, or **Current only** for a single clip.
**Settings:** the `.jsx` folder comes from the speaker profile when the clip has one, and
from the shared field otherwise. An empty field puts the script next to its XML.
**Code:** `templates/index.html:155`, `static/app/90-ae.js:140`,
`api/build.py:178`, `core/xml2ae/__main__.py:19`

### Premiere XML and DaVinci `.drp` export

**Where:** the clip row's download icon, or ⚙ › **Tools** › **Download format**.
**How:** 1. Press the download icon. 2. Choose XML or `.drp` the first time; the answer is
remembered, and later downloads use it straight away.
**Settings:** XML opens in both Premiere and DaVinci Resolve; `.drp` is a native Resolve
project with inserts and subtitles. Before export, real source timecodes are written into
the XML copy — Resolve positions clips by timecode.
**Limitations / price:** both formats have known defects, documented with symptoms in
[docs/KNOWN_ISSUES.md](KNOWN_ISSUES.md) and described byte by byte in
[docs/DRP_SPEC.md](DRP_SPEC.md).
**Code:** `api/build.py:506`, `api/build.py:549`, `core/drp.py:1`,
`static/app/40-queue.js:508`

### Render without opening After Effects

**Where:** step 3 › **Build & render**, plus the render output folder field.
**How:** 1. Tick the clips. 2. Press **Build & render**. 3. Watch the progress and the
ETA. 4. Press **Stop** to cancel.
**Settings:** the **Render output folder** defaults to `exp` next to the repository and can
come from the speaker profile. A set of several clips always becomes one AE project with
one master script and one `aerender` run; a single clip keeps the plain path. During the
**AE project build** stage of a multi-clip set, the progress display shows "N of M", which
clip is currently building, and the ETA, while already built clips in the queue show "built,
waiting for render". Batch phase timings from previous runs are remembered and make the ETA
better over time.
**Limitations / price:** After Effects must be closed: an open copy would swallow the
headless run, so the job refuses to start. There is a stall watchdog, and an instant
AfterFX exit is reported as a likely open AE copy. Rotoscoping runs during the build and is
the longest stage.
**Code:** `api/render.py:1445`, `core/aerender.py:34`, `api/render.py:1240`,
`core/aerender.py:151`, `core/aerender.py:49`, `static/app/90-ae.js:176`

### Progress, queue and logs

**Where:** the progress overlay (collapsible to a chip) and the **Logs** window behind it.
**How:** 1. Run any long job. 2. Press **Collapse** to keep working. 3. Press **Show logs**
for the raw output. 4. Press **Stop** to cancel.
**Settings:** the log window has two tabs: **Build** for server-side output and **Page
actions** for what the interface did. Queue rows show a per-file stage, a percentage during
render and the reason for a failure — the readable text of the step ("roto was not computed
for 2 of 3 chunks…"), which now reaches both the failed list and the job log instead of
staying inside the worker thread. **Stop** kills the process tree: on Windows by parentage,
on macOS and Linux by the process group, so a child model process does not stay alive
holding video memory.
**Limitations / price:** the last job of each kind (cut, build, render) is remembered on
disk, so after a server restart the interface still shows it as interrupted by a server
restart, with the item and the progress it stopped at. A cut whose process has printed
nothing for 20 minutes is marked as silent in the status and in the log, but the process is
never killed: a long speech recognition run is silent for a legitimate reason.
**Code:** `static/app/50-chrome.js:73`, `templates/index.html:530`,
`templates/index.html:553`, `core/umsg.py:1`, `api/_core.py:537`, `api/jobs.py:31`,
`static/app/00-core.js:77`

## Settings and tools

### Interface language

**Where:** the language button in the header.
**How:** Press it. The page reloads in the other language.
**Settings:** the choice is remembered; the initial language follows the OS language.
Russian is the source language, so a missing translation simply stays Russian.
**Code:** `templates/index.html:24`, `static/app/00-core.js:125`,
`static/app/00-core.js:152`, `tools/i18n_extract.py:1`

### Environment check

**Where:** the command line: `python doctor.py`.
**How:** Run it. Red rows are what stops Reelsi from starting, yellow rows are missing
optional pieces, each named together with the feature it disables.
**Limitations / price:** exit code 0 means you can work, 1 means there is something red.
**Code:** `doctor.py:1`, `doctor.py:371`

### Installer

**Where:** `powershell -ExecutionPolicy Bypass -File install.ps1`.
**How:** 1. Run it. 2. It installs the right torch build for your GPU. 3. It ends with
`doctor.py` and profile bootstrap.
**Settings:** `-Cpu` forces the CPU torch build, `-NoOptional` skips optional
dependencies. Python 3.10 and ffmpeg are expected to be present; the script says how to
install them rather than doing it silently.
**Code:** `install.ps1:1`, `core/bootstrap.py:53`, `install.sh:1`

### Command line

**Where:** the CLI entry points.
**How:** `python reelsi.py --cams 2` cuts from the console, `python -m core.aicut yellow
file.xml` marks highlights, `python -m core.xml2ae edited.xml out.jsx` builds the AE
script, `python -m core.gigaam_cut` runs the cutting engine, and `python doctor.py` checks
the environment.
**Settings:** `reelsi.py --single` for one camera and `--no-cut` for subtitles only;
`core.xml2ae` accepts music and a render folder.
**Code:** `core/cutjob.py:245`, `core/aicut/__init__.py:11`, `core/xml2ae/__main__.py:19`,
`core/gigaam_cut/__main__.py:1`

### Build verification (for developers)

**Where:** the command line.
**How:** `python -m core.verify_jsx Reelsi_out` checks every `.jsx` in a folder without
After Effects — syntax, the `CAM/SUBS/ROTO/INTRO_GROUPS/INSERTS/CAM1_SCALE` structures,
missing files, formats AE cannot import, and clip overlaps. `python tools/verify_ae.py
project.inspect.json --jsx out.jsx` compares a real AE project with what the script asked
for, using a dump taken with `tools/ae_inspect.jsx`.

The same suite holds the seams that no single check can see: every `%(key)s` placeholder of
the AE template is compared with the keys the build really passes; every style key must have
its schema field and its translation (structural checks instead of "N keys" counters);
every id the frontend looks up exists in the markup or is created by the JS, and every
function the frontend calls is defined in it. `tests/test_docs_drift.py` watches the
documentation (links with line numbers, the `static/app` file list) and
`tests/test_infra_dedup.py` forbids second copies of the infrastructure (`os.replace` only
in `core/fileio.py`, the `ffprobe` duration probe only in `core/media.py`, `subprocess.run`
and `check_output` only with a timeout). CI runs all of this on Linux and Windows, plus
`node --check` over every `static/app/*.js`: a syntax error in one of them kills the whole
page, and no Python test sees it.
**Code:** `core/verify_jsx.py:1`, `tools/verify_ae.py:1`, `tools/ae_inspect.jsx:1`

### Local API protection

**Where:** the server side; there is nothing to switch on.
**How:** the backend answers only to a page opened on this machine: a request that came from
another site is refused. The check covers the file pick dialogs (`/api/pickdir`,
`/api/pickmedia` and their neighbours) and the waveform read (`/api/waveform`) exactly like
a request that changes something — each of them opens a window or writes a cache file next
to the source.
**Settings:** deleting a clip needs an explicit confirmation in the request; without it the
server only answers with the list of files it would delete, so a stray call cannot wipe the
cut. The AI settings file, which holds the provider keys, is written with owner-only
permissions (0600) on macOS and Linux.
**Code:** `api/_core.py:217`, `api/files.py:534`, `core/aicut/config.py:264`

## Limitations

- Verified only on Windows 11 with an NVIDIA GPU and Adobe After Effects / Premiere Pro.
  Porting notes for other platforms: [docs/PLATFORMS.md](PLATFORMS.md).
- The Lumetri colour in the browser preview is an approximation: the real Lumetri curves are
  closed, so the preview shows the direction of the correction, not the exact result.
- Tritone is not applied to a bright colour of the yellow intro (luminance above 0.7): on
  such a colour it bleaches the glow instead of tinting it. Dark accent colours keep it.
- Two installed fonts may share one PostScript name. The build then probes the copies and
  takes the one that really applies; if none applies, the text is built with the default
  font and the log says which font was rejected.
- Premiere XML export and `.drp` export contain known defects:
  [docs/KNOWN_ISSUES.md](KNOWN_ISSUES.md).
- Beta status: configuration and project schemas may change between releases.
