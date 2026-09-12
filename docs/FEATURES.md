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

**Where:** step 1 › **Project folder (where the cameras are)**
**How:** 1. Put the repository next to the footage. 2. Set the project folder. 3. Press
**Rescan folders** — Reelsi lists the camera folders it found.
**Settings:** **Output folder** is where cut XML files and their sidecar files land. The
`.jsx` folder is set on step 3.
**Code:** `templates/index.html:52`, `api/files.py:16`, `webui.py:81`

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
thresholds. Extra prompt suffixes for image and video generation live in the same dialog.
**Limitations / price:** profiles are plain JSON in `speakers/`, which is gitignored. The
style in a profile is a default — you can always pick another style on step 3.
**Code:** `core/speakers.py:29`, `core/speakers.py:68`, `api/presets.py:85`,
`templates/index.html:789`

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
**Code:** `core/aicut/config.py:41`, `api/ai.py:153`, `core/aicut/catalog.py:1`,
`templates/index.html:1092`

### Model catalog and call statistics

**Where:** ⚙ › **Connections** › **Diagnostics of AI calls**; the model list is in the
**Model** field on the same tab.
**How:** 1. Press **Refresh list** to ask the provider for its models. 2. Open the
diagnostics panel to see what previous calls cost.
**Settings:** the panel groups calls by model, step and reasoning level and shows medians
of tokens, reasoning share and time. Failed calls are counted separately.
**Code:** `api/ai.py:730`, `core/aicut/config.py:38`, `core/aicut/catalog.py:21`

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
**Code:** `api/gdrive.py:271`, `api/gdrive.py:61`, `templates/index.html:32`

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
**Code:** `templates/index.html:49`, `api/files.py:56`, `core/sync.py:114`,
`core/sync.py:26`

### Audio sync

**Where:** step 1 › **Project** (the **Cams** switch) — sync runs as part of the cut.
**How:** 1. Choose 2 or more cameras. 2. Queue the pairs. 3. Sound is aligned by
cross-correlation before cutting.
**Settings:** offsets are written to `<stem>.project.json` and can be seen in the camera
layout window.
**Code:** `core/sync.py:80`, `core/sync.py:40`

### AI cutting

**Where:** step 1 › **Cut** › **AI cut** (the primary button).
**How:** 1. Fill the queue. 2. Set the output folder. 3. Press **AI cut**.
**Settings:** the CTC model listens to the whole file and returns every word with its own
timings. Then the text model gets the full transcript and returns the same text with the
parts to drop in square brackets; the code aligns that answer back to the words. Silence
longer than 0.8 s is always cut. The AI model and its reasoning level are set in ⚙ ›
**Cutting**. A draft mp4 is built at the end of every cut.
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
`templates/index.html:1003`, `static/app/40-queue.js:363`

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
**Code:** `core/breath.py:40`, `core/breath.py:227`, `core/breath.py:252`,
`api/editor.py:135`

### Cut editor

**Where:** step 1 › clips list › **Edit** (the preview window, the left half).
**How:** 1. Open a clip. 2. Click to place the cursor, drag block edges, double-click on
grey to bring cut material back. 3. Press **Save to XML**.
**Settings:** wheel zooms, Shift+wheel or the ruler scrolls, Space plays, arrows step
frame by frame. **Cut** (C), **Delete** (D) and **Undo** (Ctrl+Z) work on blocks. The
**listen to the cut-out** checkbox plays the removed audio as well.
**Limitations / price:** saving rewrites the XML and reprojects insert timings onto the new
edit; the window warns before closing with unsaved changes.
**Code:** `templates/index.html:524`, `static/app/70-editor.js:13`,
`api/editor.py:232`

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
**Code:** `core/align.py:12`, `api/build.py:206`, `templates/index.html:677`

### Draft mp4

**Where:** step 1 › **Cut** — the draft is built at the end of each AI cut; the file
lies next to the XML.
**How:** 1. Run an AI cut. 2. Press **Edit the finished ones** in the progress overlay
while the queue continues. 3. Or run a custom cut with the **Draft mp4** stage.
**Settings:** draft rendering uses 720p camera proxies cached in `_tmp`; the proxy is
built once per camera file.
**Limitations / price:** NVENC is used when available, with a fallback to CPU x264, which
is slow; the log says so explicitly.
**Code:** `core/draftrender.py:1`, `core/cutstages.py:71`, `api/jobs.py:379`,
`static/app/50-chrome.js:186`

### Temporary files

**Where:** ⚙ › **Tools** › **Temporary files** › **Clean**.
**How:** 1. Open the dialog. 2. Confirm. 3. If preview proxies exist, answer the second
question separately.
**Settings:** the dialog shows the size before deleting. Drafts and the roto cache are left
alone; preview proxies are a per-camera-file cache and are only removed if you say yes.
**Code:** `api/jobs.py:413`, `api/jobs.py:456`, `static/app/50-chrome.js:194`

### Clips list

**Where:** step 1 › **Clips**.
**How:** 1. Use **From output folder** to pick up every `.xml` in the output folder.
2. Use **Add XML…** to bring in a timeline edited elsewhere. 3. The broom clears the list.
**Settings:** the list lives in browser state and in a server mirror, so it survives a
reload and a browser change. Deleting a clip can either drop it from the list or remove
its files from disk, after a confirmation that lists them.
**Code:** `templates/index.html:107`, `static/app/40-queue.js:327`,
`api/files.py:397`

## Step 2 — Markup and inserts

### AI markup

**Where:** step 2 › **Markup**, with the per-task buttons **Subtitles**, **Highlights**,
**Inserts** and the primary **Mark up all**.
**How:** 1. Tick the clips you want (nothing ticked means all of them). 2. Press a phase
button to run just that phase. 3. Or press **Mark up all** to run subtitles, then
highlights, then inserts.
**Settings:** which model works on which task is set in ⚙ › **Markup**: a separate model
and reasoning level for highlights, inserts and intro, plus the subtitle engine.
**Limitations / price:** clips without subtitles are skipped by the highlight and insert
phases. Inserts target 10 photos and 3 videos per video, from 6 s and 10 s in.
**Code:** `templates/index.html:122`, `static/app/70-editor.js:289`,
`core/aicut/commands.py:133`, `core/aicut/commands.py:139`

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
**Code:** `core/asr_backends.py:44`, `api/editor.py:366`, `core/align.py:355`,
`static/app/70-editor.js:323`

### Word highlights

**Where:** step 2 (markup), then the clip preview › **Words** panel.
**How:** 1. Run the highlight phase. 2. Click a word to highlight it — another click
clears it. 3. Double-click sends a word to the intro. 4. Ctrl+click edits the word text
straight in the XML.
**Settings:** the highlight colour and bold are style keys; the third colour is used by
intro rows marked as accents. From the keyboard: Enter highlights, Shift+Enter sends to
intro, Ctrl+Enter edits.
**Limitations / price:** highlights are written into the XML itself, so they survive a
manual re-edit; a sidecar `.yellow.json` is kept as a fallback for words that could not be
coloured.
**Code:** `core/aicut/commands.py:50`, `static/app/85-inserts-view.js:587`,
`api/editor.py:471`, `api/editor.py:538`

### Inserts editor

**Where:** step 2, and the clip preview › **Inserts** and then **Insert editor (AI)…**.
**How:** 1. Press **Re-pick (all)** or **Fill in the missing**. 2. Drag a card block
on the timeline to move it, drag its edges to change the duration. 3. Add a photo or a
video by hand, or remove a card. 4. Press the undo icon to bring the last removed card
back.
**Settings:** **photo** and **video** buttons add cards manually; the placeholder in the
preview marks where a new card will land. A video insert plays its whole length and is
never trimmed.
**Limitations / price:** the pick respects forbidden zones: nothing in the first seconds,
nothing in the closing seconds, a minimum gap between inserts and a minimum duration.
**Code:** `templates/index.html:572`, `core/aicut/commands.py:149`,
`core/aicut/commands.py:174`, `static/app/80-inserts.js:350`,
`static/app/85-inserts-view.js:1465`

### Generating images and video for inserts

**Where:** the insert cards in the insert editor; also ⚙ › **Generation** for the profiles.
**How:** 1. Pick an image profile in ⚙ › **Generation**. 2. Press button 1 or 2 on a card
to generate an image for its query with that prompt slot. 3. Press **Missing only** to
fill every photo card that has no file. 4. For video, use the same numbered buttons on a
video card, or the **Video** tab.
**Settings:** the image profile needs an image model; the video profile needs a provider
and a video model. **Remove background** in the settings cuts the subject out of every
generated image with `rembg` and saves a transparent PNG; **Drop background** does the same
for one file on a card. The speaker profile can add a suffix or prefix to the prompt for
both image and video slots — that is what the numbers 1 and 2 choose.
**Limitations / price:** this is the paid part: the provider bills per image and per video.
The engine refuses to run without an image or video model, and generation is cancellable.
Generated media is added to the insert library and is reused for free later.
**Code:** `core/aicut/images.py:19`, `core/aicut/images.py:62`, `core/aicut/video.py:34`,
`api/ai.py:580`, `api/ai.py:641`

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
**Code:** `templates/index.html:458`, `core/aicut/video.py:16`,
`api/videogen.py:215`, `api/videogen.py:352`

### Insert library

**Where:** the insert editor › **Library**, and the clip row in the inserts window.
**How:** 1. Press **Scan** to index past projects and media folders. 2. Search by name or
description. 3. Press **Describe with AI** to have a vision model write descriptions for
files that have none. 4. Use **Import** to move downloaded media into your library folder.
**Settings:** scanned folders are typed one per line. **Auto-match after AI** makes
generation pick a library file when it can. Descriptions are just text — edit them in
place and matching follows.
**Limitations / price:** matching is semantic through LM Studio embeddings, with a token
fallback when no embedder is available. Service files are skipped: transitions, sounds,
roto masks, drafts and camera sources.
**Code:** `core/insertlib.py:1`, `api/inserts.py:85`, `api/inserts.py:127`,
`api/inserts.py:198`, `templates/index.html:712`

### Censoring

**Where:** ⚙ › **Words** › **Censorship**; the audio switch is in the same tab on step 3.
**How:** 1. Type bad stems, one per line. 2. Add ordinary words that merely contain a bad
substring to **Exceptions**. 3. Tick **Censor audio** on step 3 if the voice should dip
as well.
**Settings:** matching is by substring, so the stem `убива` covers its forms. Lines
starting with `#` are comments. Your edit goes to `badwords.user.txt` / `okwords.user.txt`
and replaces the shipped list; **Default** brings the shipped one back.
**Limitations / price:** a censored word is written into subtitles with a star instead of
its middle letter, and the audio is muted on those frames only if that switch is on. Lists
are reloaded by file modification time, so no restart is needed.
**Code:** `core/censor.py:87`, `core/censor.py:97`, `core/xml2ae/layout.py:443`,
`api/presets.py:144`, `templates/index.html:1067`, `templates/index.html:438`

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
`api/presets.py:124`

### Video caption

**Where:** the clip preview › the **Video caption** field above the words panel.
**How:** 1. Type the video caption. 2. Press **Save**.
**Settings:** the video caption is a style block with its own font, size, casing, colours and a
background plate.
**Code:** `templates/index.html:643`, `api/editor.py:47`, `static/app/85-inserts-view.js:391`

## Step 3 — After Effects

### Set files and clip selection

**Where:** step 3 › **Set files**.
**How:** 1. Tick the clips that go into the build. 2. Nothing ticked means the whole set.
3. Click a row to open the preview for that clip.
**Settings:** the broom removes ticked clips from the list only; files on disk stay.
**Code:** `templates/index.html:146`, `static/app/90-ae.js:280`,
`static/app/40-queue.js:642`

### AI intro: hook and accents

**Where:** step 3 › the clip preview › **Intro** bar, or **AI intro (n)** for the whole set.
**How:** 1. Open a clip preview. 2. Press **AI intro**. 3. Check the rows: the first rows
are the hook behind the speaker, the mid rows are accents.
**Settings:** the intro appearance mode is **word by word** or **line by line**. Rows can
be added and reordered; picking a row and clicking a word moves the group start.
**Limitations / price:** already marked-up intro is replaced after a confirmation. Intro is
cut out of subtitle words, so it is not assembled while rows are longer than one word.
**Code:** `api/ai.py:669`, `static/app/90-ae.js:392`, `core/aicut/commands.py:1`,
`static/app/85-inserts-view.js:950`

### Editing word highlights

**Where:** the clip preview › **Words** panel (the same panel as on step 2).
**How:** 1. Click a word for a highlight. 2. Double-click for an intro accent. 3. Ctrl+
click to fix the text.
**Settings:** a `|` typed between two words breaks the stack. A word that already went to
the intro is edited in its group row above.
**Code:** `static/app/85-inserts-view.js:587`, `static/app/85-inserts-view.js:609`,
`api/editor.py:538`

### Styles

**Where:** step 3 › **Setting: <clip>** › the style block, split into the tabs **Text**,
**Frame**, **Inserts**, **Layers** and **Sound**.
**How:** 1. Pick a style in the selector. 2. Edit the values you need. 3. Press **Save**,
or **Save as…** for a copy.
**Settings:** **Text** holds subtitles (words per row, rows, height, subtitle scale,
casing, colours), highlights, the subtitle plate, the caption, intro colours, glow and
shadows, the disclaimer, and the fonts. **Frame** holds the camera 1 zoom mode
(pulse, jumps, drift, none), frame fill, the top progress line and the start blur.
**Inserts** holds the photo style, animation, effects, insert positions and the continuous
rotoscope. **Layers** is the layer order, dragged with the mouse or moved with the arrow
buttons. **Sound** holds music, voice, transition, glitch and pop levels, their files and
their hit points.
**Limitations / price:** editing a style's own template and saving it applies the change to
every file in the set that uses that style. Built-in styles cannot be deleted.
**Code:** `core/styles.py:1`, `api/presets.py:15`, `templates/index.html:170`,
`static/app/95-styles.js:496`

### Subtitle scale

**Where:** step 3 › style › **Text** › **Subtitles** › **Subtitle scale, %**.
**How:** 1. Move the slider. 2. The whole subtitle precomp grows or shrinks. 3. The layout
itself is untouched: line wrapping, auto-shrinking and the stack step stay as they are.
**Settings:** the default is 100 %, the range is 20 to 200 %. It is a style key, so it is
stored in the style and arrives in AE without a manual Scale.
**Code:** `templates/index.html:198`, `core/styles.py:1`

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
`static/app/95-styles.js:670`, `static/app/90-ae.js:114`

### Layer order

**Where:** step 3 › style › **Layers** tab.
**How:** 1. Drag a row, or press the up and down arrow buttons. 2. The higher row covers
the lower ones.
**Settings:** the layers are subtitles, video inserts, rotoscope, photo inserts and intro.
The default order is subtitles, video, roto, photo, intro.
**Code:** `static/app/95-styles.js:493`, `static/app/95-styles.js:496`,
`templates/index.html:374`

### Photo inserts: animation, effects and position

**Where:** step 3 › style › **Inserts** › **Photo**.
**How:** 1. Pick the photo insert style: auto, camera 1 (from behind the shoulder) or
camera 2 (push-in with blur). 2. Pick the animation: push-in with blur, rise from below
with a fade, or none. 3. Pick the effect preset: the new one (black shadow plus rounded
mask), the old one (white shadow plus choker) or none. 4. Adjust the resting position per
camera in percent of frame.
**Settings:** camera 1 inserts inherit the camera 1 zoom, so their offset scales with it.
When the **Camera 1** style lands on a camera 2 piece, a separate offset applies.
**Limitations / price:** while a rising insert is on screen the subtitles step aside. A
photo that crosses a camera change is trimmed exactly at the change; video inserts are
never trimmed.
**Code:** `templates/index.html:351`, `static/app/85-inserts-view.js:696`

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
**Code:** `core/roto.py:1`, `core/roto.py:41`, `templates/index.html:364`

### Music

**Where:** step 3 › **Folders and settings** › **More — brightness, music, censoring, folders** › **Music (−20 dB)**.
**How:** 1. Choose **random from downloads**, **YouTube link** or **file**. 2. For a link,
paste the URL. 3. For a file, press **Browse…**.
**Settings:** the music folder for downloads is set next to it. The level lives in the
style's **Sound** tab.
**Limitations / price:** a YouTube link requires `yt-dlp`; a random track picks one file
from the music folder at build time.
**Code:** `templates/index.html:441`, `core/ytmusic.py:10`, `core/ytmusic.py:33`,
`api/files.py:355`

### Scene preview in the browser

**Where:** step 3 › **Preview — intro, highlights, inserts**.
**How:** 1. Open the preview. 2. Watch the edit with inserts, intro, subtitles and sound.
3. Drag an insert block on the timeline to move it, drag its edges to change the duration.
4. Drag the zoom target point.
**Settings:** the preview uses the same scene plan that goes into the build — the plan is
served by `/api/scene` without GPU work. The style block moves into the preview's **Style**
tab while it is open, and returns to the page when it closes. Preview volume is shared by
all players, and the music and voice levels in the preview are the same numbers that end
up in the `.jsx`.
**Limitations / price:** camera proxies are built once per camera file to keep seeking
snappy; until a proxy is ready the preview plays the original. Fonts for the preview come
from the installed system fonts.
**Code:** `api/build.py:483`, `static/app/85-inserts-view.js:55`,
`api/previewproxy.py:66`, `api/files.py:259`, `static/app/50-chrome.js:328`

### Exporting `.jsx`

**Where:** step 3 › the sticky build bar.
**How:** 1. Tick the clips. 2. Choose **Separate .jsx** or **One for all**. 3. Press
**Build set**, or **Current only** for a single clip.
**Settings:** the `.jsx` folder comes from the speaker profile when the clip has one, and
from the shared field otherwise. An empty field puts the script next to its XML.
**Code:** `templates/index.html:152`, `static/app/90-ae.js:140`,
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
**Code:** `api/build.py:375`, `api/build.py:402`, `core/drp.py:1`,
`static/app/40-queue.js:503`

### Render without opening After Effects

**Where:** step 3 › **Build & render**, plus the render output folder field.
**How:** 1. Tick the clips. 2. Press **Build & render**. 3. Watch the progress and the
ETA. 4. Press **Stop** to cancel.
**Settings:** the **Render output folder** defaults to `exp` next to the repository and can
come from the speaker profile. A set of several clips always becomes one AE project with
one master script and one `aerender` run; a single clip keeps the plain path. Batch phase
timings from previous runs are remembered and make the ETA better over time.
**Limitations / price:** After Effects must be closed: an open copy would swallow the
headless run, so the job refuses to start. There is a stall watchdog, and an instant
AfterFX exit is reported as a likely open AE copy. Rotoscoping runs during the build and is
the longest stage.
**Code:** `api/render.py:1943`, `api/render.py:1907`, `api/render.py:172`,
`api/render.py:69`, `static/app/90-ae.js:176`

### Progress, queue and logs

**Where:** the progress overlay (collapsible to a chip) and the **Logs** window behind it.
**How:** 1. Run any long job. 2. Press **Collapse** to keep working. 3. Press **Show logs**
for the raw output. 4. Press **Stop** to cancel.
**Settings:** the log window has two tabs: **Build** for server-side output and **Page
actions** for what the interface did. Queue rows show a per-file stage, a percentage during
render and the reason for a failure.
**Code:** `static/app/50-chrome.js:73`, `templates/index.html:748`,
`templates/index.html:771`, `core/umsg.py:1`

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
**Code:** `doctor.py:1`, `doctor.py:369`

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
**Code:** `reelsi.py:278`, `core/aicut/__init__.py:11`, `core/xml2ae/__main__.py:19`,
`core/gigaam_cut/__main__.py:1`

### Build verification (for developers)

**Where:** the command line.
**How:** `python -m core.verify_jsx Reelsi_out` checks every `.jsx` in a folder without
After Effects — syntax, the `CAM/SUBS/ROTO/INTRO_GROUPS/INSERTS/CAM1_SCALE` structures,
missing files, formats AE cannot import, and clip overlaps. `python tools/verify_ae.py
project.inspect.json --jsx out.jsx` compares a real AE project with what the script asked
for, using a dump taken with `tools/ae_inspect.jsx`.
**Code:** `core/verify_jsx.py:1`, `tools/verify_ae.py:1`, `tools/ae_inspect.jsx:1`

## Limitations

- Verified only on Windows 11 with an NVIDIA GPU and Adobe After Effects / Premiere Pro.
  Porting notes for other platforms: [docs/PLATFORMS.md](PLATFORMS.md).
- Premiere XML export and `.drp` export contain known defects:
  [docs/KNOWN_ISSUES.md](KNOWN_ISSUES.md).
- Beta status: configuration and project schemas may change between releases.
