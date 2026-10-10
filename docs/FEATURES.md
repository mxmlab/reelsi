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

### Second text pass: Whisper corrects the spelling

**Where:** ⚙ › **Cut** › **Cutting engine (text)** (off by default); it acts on the next **AI cut**.
**How:** 1. Pick a Whisper engine in **Cutting engine (text)**. 2. Run **AI cut** as usual.
**Settings:** Whisper transcribes the same file again, and only the spelling of GigaAM's words
changes, and only when both engines agree in time (within 0.3 s) and in letters. The timings stay
GigaAM's. A word Whisper swallowed stays as GigaAM has it; a word Whisper made up in silence is
dropped; repeats are kept, because cutting needs them. Whisper large-v3-turbo is in the engine list
as well (about four times faster than large-v3). With the setting on, the subtitles are written into
the XML during the cut, together with `.words.json` and `.srt`, so step 2 does not recognise the speech
again. When a piece is brought back in the step 1 editor, its subtitles come back from the source
words, and hand edits of words are kept.
**Limitations / price:** the pass runs under its own GPU lock; if Whisper fails, the cut goes on with
GigaAM's text. Measured on 49 clips with the owner's corrections: large-v3 fixed 309 words and broke 30,
large-v3-turbo fixed 222 and broke 63. Russian fine-tunes of Whisper did worse than the stock models.
**Code:** `core/gigaam_cut/textpass.py:37`, `core/asr_merge.py:65`, `core/cut_subs.py:230`,
`core/aicut/config.py:712`

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
**listen to the cut** checkbox plays the removed audio as well. There is now **one player
here** — the editor: the "Montage" block with a second player is gone, and the volume and
the "camera — cuts — length" line moved into its control row. Clicking far along the
timeline during playback puts the playhead exactly where you clicked (the second player
used to roll it back to the next montage piece).
**Processed speaker voice** (the denoiser and the live VST plug-ins) plays in this same
player, both while listening to the montage and while playing the source. The sound time is
the source time of camera 1 under the playhead, so over the cut-out places the sound jumps
with the picture; the level is set by the player row slider (see "Speaker voice" below).
**Limitations / price:** saving rewrites the XML and reprojects insert timings onto the new
edit; the window warns before closing with unsaved changes.
**Code:** `templates/index.html:306`, `static/app/70-editor.js:13`,
`api/editor.py:256`

### Speaker voice

**Where:** step 1 › cut preview › the **Voice** panel (the speaker profile must have voice
processing on); on steps 2–3 the same panel sits with the inserts preview.
**How:** 1. Turn on the **AI denoiser** in the speaker profile and/or add VST3 plug-ins.
2. Open the preview — the processed voice track plays in the player. 3. Turn the denoiser
and plug-in knobs by ear; **Configure** opens the plug-in window.
**Settings:** the chain runs "denoiser first, then plug-ins", like a track in a DAW. The live
host exists only while a plug-in window is open ("turn the knobs and listen"): close the
windows and the host hands the state of EVERY plug-in back to the speaker profile, the voice is
re-baked, and all three steps play that one baked track — the very track that goes to AE. While
the denoiser is being computed (or is off), the plug-ins play LIVE over the camera sound — the
frame says so; once it is ready the host swaps the track for the cleaned one at the same
position. The baked voice is a versioned file (`<stem>.voice.<key8>.wav` with a `.voice.json`
sidecar), so replacing it never fails on Windows while the preview, `/api/media` or an open
AE/Premiere project holds the old one; a failed bake is not silent — the preview shows a toast
and a "voice without processing" line, and the build warns with the reason. Changing plug-ins or
their knobs never recomputes the denoiser (it is computed once and cached). A plug-in that
fails to load is skipped, and its name and reason are shown in the panel.
**The voice track across cuts** is carried by an `<audio>` double that repeats the picture's
decision at every seam (the picture doubled → the voice does too; the picture seeked → the voice
waits for `seeked`), and drift is killed with speed (±6 %), not with a seek. The rule that holds
it: a double always plays the very same file as the live track, so the previous clip's voice can
never sound on the next seam.
**Volume sliders** — **Volume** in the panel (the clip style's `voice_db`, the same value
as the **Voice** slider on steps 2–3) and the listening volume in the player row both act
on the live plug-in sound as well: the host's volume is the speaker's voice volume (dB)
plus the listening volume. The live host plays at the device's rate and reports a failure
in the panel, giving the sound back to the page; **Configure** opens the window of the host
that is already playing, closing that window does not break the sound, and closing the
preview takes the host down.
**The cut** listens to the voice after the WHOLE speaker chain: the denoiser (when on) and
the enabled plug-ins — through the same code as the output. If a plug-in fails, the cut
falls back to the denoise track and then to the raw sound.
**Preview sound, the graph and the browser.** The preview wakes the Web Audio graph through
ONE door (`audioWake`) from the click handler of every player, and it puts the camera sound
into the graph only when there is something to process (a processed voice track, the live
plug-in host, or a voice volume other than 0). A speaker without processing therefore plays the
camera audio directly and does not depend on the browser's audio state. Firefox does not decode
PCM (`pcm_s16be`) inside an MP4 at all, unlike Chromium; while the preview plays the source,
the player says so in its own row — "Firefox cannot read the sound of these cameras — the sound
will appear with the proxy" — from a fact (`<video>.mozHasAudio === false` after 1.5 s of
playback), and the row goes away by itself once the video proxy (with an aac track) is playing.
There is NO separate audio proxy: the one that used to exist played on top of the camera sound
in Chromium and was removed. The recommended browser is Chromium-based (Chrome, Edge, Brave).
VST3 support comes from the optional `pedalboard` package; without it the voice panel still
works, there are simply no plug-ins, the camera audio plays as is, and the output device list
comes from the sound system with a printed reason.
**Limitations / price:** an NVIDIA card and the denoiser model are required; the plug-in
window floats above the other windows while the sound plays.
**Code:** `core/voicefx.py`, `core/voicefx_editor.py`, `static/app/60-preview.js`

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
**Code:** `core/draftrender.py:1`, `core/cutstages.py:71`, `api/jobs.py:482`,
`static/app/55-progress.js:145`, `static/app/55-progress.js:362`

### Temporary files

**Where:** ⚙ › **Tools** › **Temporary files** › **Clear**.
**How:** 1. Open the dialog. 2. Confirm. 3. If preview proxies exist, answer the second
question separately.
**Settings:** the dialog shows the size before deleting. Drafts and the roto cache are left
alone; preview proxies are a per-camera-file cache and are only removed if you say yes.
**Code:** `api/jobs.py:528`, `api/jobs.py:571`, `static/app/50-chrome.js:92`

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
the same dialog as the cross on a clip row: **Remove from the list** or **To trash (can be restored)…**; the second one
shows the combined list of cut files with their sizes and then safely moves them into the `_reelsi_trash/`
folder alongside the XML. Deleted clips can be restored at any time via the **Recycle Bin** modal.
The source camera video is never touched, and one failing clip does not stop the others.
Clip work is continuously saved to `<stem>.clip.json` on disk, allowing full state recovery
(including intro, style, speaker, and inserts) even if the browser cache is wiped.
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
and lasts longer. Gaps left by earlier deletions do not close by themselves. The particle
**"не"/"ни"** before a word the AI picked is highlighted TOGETHER with it (a measurement
over 236 clips found 135 highlighted words with a white "НЕ" in front of them) — otherwise
the negation stayed white and the point of the highlight was lost. Prepositions and other
function words are not joined, and the same rule is in the AI hint.
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
the edge of the frame uncovers the camera shot under it. **Switching the type** (the
PHOTO/VIDEO badge on a card) switches WHAT is searched: the stock and library options of
the previous type are cleared, the open panels are searched again for the new type, an
auto-picked file of the old type is removed and a new one is picked, while a file chosen by
hand or generated stays. The badge changes at once, and a second click during the search
does not switch back. Timing and duration adjusted by dragging in the step 3 preview are
saved into the clip and survive a reopening. The **Inserts** window fills the screen and
keeps one height for any number of inserts: the list of cards scrolls inside itself while
the player, the buttons and the timeline stay put.

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

### Stock photos and video

**Where:** the insert card › **Stock** (the query of the card is searched on the stock sites, and the
candidates are shown for choice).
**How:** 1. Press **Stock** on a card that has no file yet. 2. Look at the candidates. 3. Pick one: it is
downloaded into the library, and the next clips find it there without a stock.
**Settings:** the order is Pexels → Unsplash → Pixabay → Openverse → Coverr. The search walks the list
until it has enough candidates; the order was chosen by a measurement on 30 of the owner's queries.
Keys are in ⚙ › **Generation** › **Stock**; Openverse needs no key. Coverr (video) stays behind
`REELSI_STOCK_COVERR=1` until a live key has been checked, and `REELSI_STOCK_OFF` switches providers off.
The query is written by the AI as the object and one or two visible features in English, without numbers,
doses or labels: "syringe with small 250 mark" brought motorcycles from Pixabay.
**Limitations / price:** search answers are cached for 24 hours. A refusal from a stock (Unsplash's demo
limit, 429 or 403) moves the search on to the next stock. Openverse keeps only the licences that allow
commercial use and modification. Each downloaded file gets a `.license.json` next to it with the provider,
the author and the licence.
**Code:** `core/stock.py:67`, `core/stock.py:171`, `api/inserts.py:316`, `static/app/80-inserts.js:804`

### Inserts by name

**Where:** the speaker profile › **Inserts by name** (on by default). The dictionary is `named_inserts.json`
in the repository root; it is not in git.
**How:** 1. Copy `data/named_inserts.example.json` to `named_inserts.json` and fill it with the names you use.
2. Run the AI markup on the clip. 3. When a name is spoken and the library has a picture for it, a photo
insert appears at that word.
**Settings:** a key is the object's name; its value lists the forms used in speech and in the library
(endings are matched by the word stem). `_prefer` lists words the picture must contain (`коробк*` matches
the start of a word); `_avoid` lists story words that spoil a picture; `secondary` lists forms such as an
older name of a brand, which are searched only when the main names find nothing. Among the pictures, a name
in the file name beats a name only in the description, and a main name beats a secondary one.
**Limitations / price:** without the dictionary the feature is off silently. A name is not placed closer
than 4 s to another insert, the same name is used at most once per 20 s, and the photo lasts 2.5 s by
default. Named inserts count in the photo quota and are not pushed out by it; when the photos exceed the
quota, the weakest AI photos go first.
**Code:** `core/aicut/commands.py:578`, `core/aicut/commands.py:285`, `core/insertlib.py:1015`

### Background cut-out model

**Where:** ⚙ › **Generation** › **Background removal model** (next to **Remove background**).
**How:** 1. Keep **u2net** for speed (about 0.7 s a picture). 2. Choose **BiRefNet** for cleaner edges of
hair and small objects. 3. After a change, a picture is cut out again: the cache records which model made
each file.
**Settings:** the setting is `rembg_model` in `ai_config.json`. BiRefNet takes about 8 s a picture on a
processor and downloads its model of about 1 GB into the rembg folder on first use.
**Limitations / price:** on 12 pictures of the library, BiRefNet did better on 3, worse on 1, and left less
translucent haze on 9 of 12. A cache file without a model mark counts as u2net.
**Code:** `core/aicut/images.py:58`, `core/insertlib.py:1115`, `core/stock.py:275`

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

### Disclaimer: text, scale and position

**Where:** style › **Text** tab › the **Disclaimer** layer, knobs in the **Transform** group;
the disclaimer itself shows in the clip preview at the bottom of the frame.
**How:** 1. Switch the layer on and type the text (empty means the default text). 2. Adjust
**Scale, %** and **Position by height, % of frame**. 3. Need a sideways shift — **Horizontal
shift, px**. 4. The **Repeat at the end** tick puts a copy at the end of the clip.
**Settings:** **Scale, %** (30…300, default 100) multiplies the fitted size: the size itself
shrinks to fit the frame width, so a narrow font does not inflate the disclaimer.
**Position by height, % of frame** (default 76.4) is the baseline of the first line, exactly
like the Position of a text layer in After Effects. **Horizontal shift, px** is measured from
the centre of the frame. **Disclaimer line gap, px** sets the line step from glyph outlines.
**Limitations / cost:** the preview draws the disclaimer with the same numbers the `.jsx` gets
— size, position, line step, the opacity fade (100 until the end of the show minus 0.35 s,
then linear to zero) and the glow; the picture matches the build, but curves and fonts in
motion still need a render to be judged. The end copy extends the comp by 1.35 s.
**Code:** `core/xml2ae/plan_decor.py:446`, `static/app/85-inserts-view.js:1431`,
`core/styles.py:245`

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
be added and reordered; picking a row and clicking a word moves the group start. The **add a
word to the left** button takes the previous word into the row (for an accent it moves the
start); a row that became empty is deleted and the group start moves with it. The look of the
rows (animation and effect) is chosen by the AI by rules derived from the owner's hand-made
edits (12 clips, 400 rows, an 82 % match): an accent is a glitch, a background row of 2+
words rises, a group of 4+ rows reveals the first row and moves the rest in from the right, a
white row of 2–3 words rises, everything else has no animation. "Не", prepositions and
dependent words stay with their word in a row and in accents. **Glow of accent rows** is a
single style switch (see "Glitch glow" below): the style decides the glow of a row, not the
AI's markup.
**Line length and the call word:** the style's **Intro line length** (group Intro, default 20 characters)
sets how long a hook row may be. The AI breaks a hook row only when it is longer, and only between
meaningful pieces; a hook ends on a finished thought, not on a conjunction, a preposition or a pronoun.
"Не" and "ни" at the end of a row always move to their word; a preposition moves only before a row of
another colour. If the video ends by asking the viewer to write a word in quotes, that word is the last
accent, and nothing follows it.
**Limitations / price:** already marked-up intro is replaced after a confirmation. Intro
words are cut out of the subtitles, so they do not show up in the subtitle rows either; this
works the same in the row mode and in the word-by-word mode.
**Code:** `api/ai.py:463`, `static/app/90-ae.js:283`, `static/app/90-ae.js:392`,
`core/aicut/commands.py:1`, `core/aicut/commands.py:941`, `core/aicut/commands.py:1376`,
`core/aicut/prompts.py:130`

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
**Frame** holds a section per camera: the zoom mode
(push-in with recoil, hard jumps, drift, none), long-take and highlight zooms, highlight
strength, frame fill, the zoom point, the frame offset, the horizon, head tracking, the
Lumetri colour and camera 2's link to camera 1's colour, the top progress line and the
start blur.
**Inserts** holds the photo style, animation, effects, insert positions, the plate image and
the continuous rotoscope. **Layers** is the layer order, dragged with the mouse or moved
with the arrow buttons. **Sound** holds music, voice, transition, glitch and pop levels,
their files and their hit points.
**Limitations / price:** editing a style's own template and saving it applies the change to
every file in the set that uses that style. Built-in styles cannot be deleted.
**Code:** `core/styles.py:1`, `api/presets.py:15`, `templates/index.html:170`,
`static/app/95-styles.js:496`

### Camera 1: zoom animation, take zooms and highlight zooms

**Where:** step 3 › style › **Frame** › **Camera 1** › **Zoom animation**.
**How:** 1. Pick the mode: **push-in with recoil (smooth)**, **hard jumps 100–140%**,
**drift 100–160% (smooth between cuts)** or **no zoom (static frame)**. 2. In the **hard
jumps** mode set **punch-in at start**, **First punch-in, %**, the **Inside a long take**
block and the **Highlight** checkbox.
**Settings:** "Zoom animation" is three independent blocks: **At the start of the clip**
(checkbox + scale), **On a cut** (none / push-in with recoil / jump / jump + drift, the
scale from–to on one line) and **Inside a long take** (take length, **Zoom in by, +%**
from–to, **Hold, s**, **Highlight**). **Scale, %** is always the frame size, and **Zoom in
by** is the only addition, signed "+"; a range written "from > to" swaps itself. In **hard
jumps** the scale jumps to a random value between **Pullbacks from, %** and **Pullbacks up
to, %** at every cut. **punch-in at start** opens the clip with a smooth approach from
**First punch-in, %** down to the first jump instead of starting on a random value.
**Inside a long take** adds a zoom in every take longer than the threshold — in ANY cut
type, not only jumps: the camera moves in, holds **Hold, s** and pulls back; the cycle
shrinks to fit a short take (the reserve, the offset, the approach/pullback and the hold),
and when even the smallest one does not fit there is no zoom and the camera is not left
zoomed in. **Highlight** zooms on the highlighted words (the clip's yellow words and the
intro words coloured as accents): consecutive words count as ONE phrase, the approach
starts 0.1 s before the first word, and the hold lasts to the end of the last one plus
**Hold, s**. The pullback is placed only when it fits entirely before the cut — otherwise
the camera holds the zoom up to the cut. **Only strong highlights** zooms on the strongest
phrases rather than on every one (see "Highlight strength"). **Limitations / price:** in a
short take the hold of **Hold, s** is shorter.
**Code:** `core/xml2ae/plan_camera.py:118`, `core/style_schema.py:1331`, `core/styles.py:184`

### Camera 2: its own zoom, frame and colour

**Where:** step 3 › style › **Frame** › the **Camera 2** section (right under "Camera 1").
**How:** 1. Set camera 2's **Transform** — **Frame fill, %**, **Zoom point**, **Frame
offset X/Y, px**, **Horizon, °**. 2. In **Zoom animation** pick the mode (**no zoom** by
default, which keeps the build as before) and set the zooms. 3. The **Colour (Lumetri)**
group has a link button in its header: linked keeps the fields grey and reading camera 1's
colour, unlinked makes them its own.
**Settings:** camera 2 has a full set of its own: the zoom keys are computed separately
(zooms and jumps sit on the cuts onto camera 2, take and highlight zooms are camera 2's
own); camera 1's settings no longer affect camera 2 and the other way round. The fill
multiplies the zoom keys; with "no zoom" the frame sits at the given scale. The offset goes
into the Position of camera 2's null, the rotation turns its layers and its roto; the
intro, the inserts and head tracking on camera 2 ride the frame. **Camera 2 is active**
means its zoom is on or any Transform field is off default — that decides the intro's
parent on camera 2 and the target of the zoom-point picker. A style that had the old "Zoom
camera 2 too" checkbox gets a copy of camera 1's settings once on load, so the look does
not change.
**Code:** `core/xml2ae/plan_camera.py:143`, `core/style_schema.py:1234`,
`static/app/94-stylepanel.js:1314`

### Highlight strength: zoom on strong words only

**Where:** step 3 › style › **Frame** › **Highlight strength** (a group shared by both
cameras).
**How:** 1. Turn on **Only strong highlights** for the camera you want (in its **Highlight**
block). 2. Pick the **Measurement** — **by emotion** or **by voice**. 3. Set the
**Strength threshold, %**, **Zooms per piece, max.** and **Second zoom — take from, s**.
**Settings:** the strength of a word is computed automatically (the sidecar
`<stem>.emph.json` next to the XML) and has two halves: the **emotion of the phrase** —
GigaAM-Emo measures how non-neutral the phrase around the word is (a 2.5 s window, 65 ms
per word on the GPU); the **stress of the word by sound** — loudness, pitch and syllable
length compared with the neighbouring words. Both estimates sit side by side, so switching
the way recomputes nothing, and in "by voice" mode the emotion model is not loaded at all.
The **Strength threshold, %** is a percentile of the CLIP's own highlight strengths: 70 (the
default) zooms on words no weaker than the clip's p70, 50 is the median, 0 zooms on every
phrase with highlights and 100 only on the clip's strongest word. The text stays yellow
either way: the threshold is about the camera, not the colour. **Zooms per piece, max.**
caps the zooms in one piece (1–3), and **Second zoom — take from, s** sets the take length
from which a second zoom is allowed. A phrase is consecutive highlighted words and its
strength is the maximum of its words; zooms are placed in timeline order.
**Limitations / price:** when the strength is not computed (no sound, no model, a word
added by hand after the calculation), the rule behaves as before — a zoom on every phrase —
and the log says "highlight strength not computed". The calculation runs after the
highlight markup and lazily during the build; in the preview it is started by **Compute
roto and tracking** together with the roto and the head track.
**Code:** `core/emphasis.py`, `docs/HIGHLIGHT_SPEC.md`

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

**Where:** step 3 › style › **Frame** › **Transform** › **follow the head** — every camera
has its own block, so camera 2 can follow the head too.
**How:** 1. Tick **follow the head**. 2. Set **Head X, %** — where the head should sit in
the frame. 3. Set **Follow smoothing, s** and, if needed, **Follow from zoom, %**.
4. Build the set.
**Settings:** the frame moves horizontally to keep the head at the chosen place. The head is
found by the person mask — the same Robust Video Matting that rotoscope uses — and not by
face detection, so a portrait on the wall does not confuse it. The track is computed on the
GPU during the first build of a clip and cached next to the XML as `<name>.head.json` (camera
2 keeps its own `<name>.head2.json`); later
builds just read the cache. **Follow smoothing, s** is the time constant of the movement.
**Follow from zoom, %** switches the tracking on only from that zoom value: below it the
frame smoothly returns to its place, 0 means always follow. The threshold is compared in the
same numbers as the jump and take ranges, without the frame fill. Inserts and the intro on
that camera travel with the frame; on camera 2 the cut-away frame is tracked over its own
source.
**Limitations / price:** the correction is limited by the frame itself: the edge of the
picture never opens (the shift is clamped along AE's curve and by the smallest scale on the
segment, and the track is keyed at every sample, 10 per second, linearly). Tracking needs the
GPU and the matting model (downloaded on first use, as for rotoscope); if it fails, the build
continues without tracking and says so in the log.
**Code:** `core/headtrack.py:201`, `core/xml2ae/layout.py:1156`,
`core/xml2ae/precompute.py:63`

### Colour (Lumetri)

**Where:** step 3 › style › **Frame** › **Color (Lumetri)** (the group has its own
checkbox); camera 2 has its own group with a link button.
**How:** 1. Tick the group. 2. Set the nine parameters — **Exposure**, **Contrast**,
**Highlights**, **Shadows**, **Whites**, **Blacks**, **Temperature**, **Tint** and
**Saturation**. 3. Build the set.
**Settings:** one Lumetri Color effect goes on every camera clip and on its rotoscope copy,
with the same nine values as the Lumetri panel in After Effects. The per-clip exposure of
the AE step is added to **Exposure**, so the two do not fight. Camera 2's group starts
**linked** to camera 1: its fields are grey and show camera 1's colour, and edits of camera 1
show at once. Unlink it and the fields take camera 1's current values as their own — the
build then writes `LUMETRI2` on camera-2 clips and roto copies, and the preview tints camera
2 with its own filter. The group's **Reset** links it back.
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
still in the frame and the group is fitted to the frame width — grown and shrunk alike, while
the attached one is only shrunk; a group whose scale was set by hand is left alone either way.

**Edge margin and scale are per camera.** **Edge margin, %** (`intro_margin` / `intro_margin2`,
0 to 30, 4 by default) is the gap on EACH side of the frame, and the width share comes out of it
as `1 − 2·margin/100`; **Intro scale cap, %** (`intro_fit_max` / `intro_fit_max2`, 100 to 1000,
250) is the ceiling of the GROWTH — without it one short word blew up to 667–819 % of the frame,
while shrinking is not limited. Both knobs are always visible: a group that lands on a cutaway
takes the `*2` keys, decided in one place (`plan_intro`). Before, the width and the cap were one
value for both cameras, and an attached intro was shrunk to a constant, so one phrase came out
at different sizes on camera 1 and camera 2. After the fit the group is lowered by its actual
top, the big word included, and never rises above the safe line of the frame
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

**The intro precomp shadow opacity is a percent.** **Shadow opacity, camera 1, %**
(`intro_comp_shadow_opacity`) and **camera 2, %** (`intro_comp_shadow2_opacity`) run 0 to 100,
where 100 % is the shadow at full strength. The knobs were labelled "Transparency" with a `%`
sign while holding AE's RAW 0..255 value: picking "50 %" gave 20 %, and the default 68 (27 %)
read as "68 %" and looked like "the shadow does nothing". The percent is converted in one place
(`core/xml2ae/plan_style.py`, `opacity*255/100`), and both the `.jsx` and the preview take the
number from there. Old styles migrate on read as `op/255*100` (68 → 26.7, 31 → 12.2,
255 → 100), so already-built clips keep their shadow; the personal style files are not
rewritten, and for default values the built `.jsx` is byte-for-byte the old one. The shadow
colour (**Shadow fill**, `intro_comp_shadow_fill`), its offset and its softness sit next to it,
in the same groups.

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
After Effects take the same numbers. **The top of such a group stays put:** it used to hold on
to the baseline when shrunk and sat lower than the neighbouring groups (the owner saw it as
"the group is lower than the rest"); now the top of the block matches the top the same group
would have in the ordinary layout (`layout.intro_big_top_shift`, taken from the topmost row).
Such a group stays smaller by its geometry — it is a horizontal block in the same width — and
smaller edge margins are what make it larger. If several rows of a group are ticked, the big
one is the first of them; a group of one row has no big word. A clip without such a row builds
byte-for-byte as before.
**Code:** `core/xml2ae/layout.py:322`, `core/xml2ae/layout.py:166`,
`core/styles.py:232`, `core/style_schema.py:479`, `core/style_schema.py:577`,
`static/app/94-stylepanel.js:1514`

### Glitch glow: built-in or Deep Glow 2

**Where:** Settings (⚙) › **Tools** › the **After Effects** block › **Glitch glow**.
**How:** 1. Open ⚙ › **Tools**. 2. Under **After Effects**, choose between **Built-in (Blur + Glow)** and **Deep Glow 2 (plugin)**. 3. Rebuild `.jsx` scripts for clips if already exported.
**Settings:** controls how yellow intro words with the "glitch" animation glow in the generated After Effects project. Built-in uses Gaussian Blur and Glow available in every AE install (default). Deep Glow 2 replaces them with the third-party plugin using pre-tuned parameters; accent lines and other lines remain untouched. In this mode the plugin is not put on a line that has the line glow of its own (tick **Deep Glow with line glow**, `intro_dg_with_glow`, off by default, to get the old behaviour back) and not on a bright highlight colour — the same Rec.709 brightness threshold (above 0.7) as for Tritone, so such a word keeps the built-in Blur + Glow. Saved under the `glitch_glow` key in `ai_config.json` (`builtin` or `deepglow2`) and takes effect on the next build; already built `.jsx` scripts need to be rebuilt.
**Limitations / price:** if Deep Glow 2 is selected but the plugin is not installed in After Effects, manual build shows a single dialog per file reporting the number of unstyled words, while headless rendering writes an error line to the log; words remain without glow. There is no automated pre-flight check or fallback to built-in effects.
**Code:** `core/aicut/config.py:524`, `core/aicut/config_actions.py:221`, `core/xml2ae/build.py:594`, `core/xml2ae/build.py:1491`, `templates/index.html:783`, `static/app/10-settings.js:732`

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

### `.jsx` and render folders per speaker

**Where:** step 1 › speaker selector and the speaker profile dialog (**.jsx folder**,
**Render output folder**); the **Folders and parameters** card on step 3.
**How:** 1. Set the `.jsx` folder in the speaker profile. 2. Tag the clips with that
speaker. 3. Build: every clip goes into its own speaker's folder. The caption under the
field says which rung of the ladder the folder came from.
**Settings:** the folder of a tagged clip is looked up in one ladder — the profile's
`.jsx folder`, then the profile's **result folder** (the cut folder of the same speaker),
then the folder of the clip's own XML. The shared **`.jsx folder`** field on step 3 is used
only by clips without a speaker tag. The render folder follows the same ladder without the
XML rung: the profile's **Render output folder**, otherwise the shared field (the server's
`exp` default). Editing the field on a clip with a tag writes into that speaker's profile
after confirmation.
**Limitations / price:** a clip tagged with a speaker no longer falls back to the shared
field, so the folder of one speaker can never leak into another's clips.
**Code:** `static/app/95-styles.js:99`, `static/app/90-ae.js:240`,
`api/build.py:84`

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

**Where:** step 3 › style › **Inserts** › **Rotoscope**; in the clip preview the **Compute
roto and tracking** button (words/intro tab, at the top).
**How:** 1. Tick **Auto rotoscope** (and/or **follow the head**). 2. Choose the device and
the mask bottom. 3. Press **Compute roto and tracking** in the preview to see the result
without building the set. 4. Or just build the set.
**Settings:** the mask is computed by Robust Video Matting on the GPU; the mask bottom
percentage cuts the mask, and **Roto on Camera 1 only** skips camera 2 pieces. The compute
button is shown when the style has roto or tracking on (with both off, or only camera 2's tracking with
one camera in the XML, there is nothing to compute: the server refuses before it takes the GPU, and the
button is recomputed on every style change); it computes the masks and the head
track with per-chunk progress and a **Stop** button, and marks itself "computed" when done.
The preview then shows the **speaker's cut-out figure**: the camera frame multiplied by the
mask, above the intro in the style's layer order and in sync with the player; tracking
appears from the plan right after the calculation. The calculation takes the shared job lock,
so it never runs on top of a cut, a build or a render, and the build reuses what it computed
(the caches are shared).
**Limitations / price:** the model is downloaded on first use, and masks are cached, so a
second build reuses them. RVM is released from video memory right after the calculation. On
a VRAM shortage the build stops with a clear message and the XML is not overwritten. Add the
photo and intro layers below roto to bring the person in front of them.
**Code:** `core/roto.py:1`, `core/roto.py:41`, `core/style_schema.py:1768`, `api/previewcalc.py:156`,
`templates/index.html:419`

### Music

**Where:** step 3 › **Folders and parameters** › the **Music** block under **More —
brightness, music, censoring, folders**.
**How:** 1. Choose **as in the style** or **own track**. 2. For your own track pick what
to use: **no music**, **random from the folder**, **file** or **YouTube link**. 3. For a
file press **File…**, for a link paste the URL and press **Download**. 4. In random mode
the chosen track is shown by file name next to the block — press **Another track** to pick
a different one.
**Settings:** the music mode, the tracks folder and the track itself are keys of the style
(next to the level on the **Sound** tab), so every style carries its own music, and the
clip only overrides it. The shared folder field is empty by default, which means the
`music` folder next to the project. The picked random track is stored on the clip and both
the preview and the build play exactly that file: there is no second independent pick. The
level lives in the style's **Sound** tab.
**Limitations / price:** a YouTube link requires `yt-dlp`; the link is downloaded by the
**Download** button, and until then the build would download it itself at build time.
Changing the mode or the folder drops the pinned track and picks a new one. A random track
is picked deterministically from the clip's XML path, so the same clip keeps the same track
across rebuilds.
**Code:** `core/styles.py:242`, `core/style_schema.py:2725`, `core/ytmusic.py:17`,
`api/files.py:582`, `static/app/95-styles.js:1979`

### Scene preview in the browser

**Where:** step 3 › **Preview — intro, highlights, inserts**; the same player opens a clip
from step 1, and there the window is titled with the name of the open file.
**How:** 1. Open the preview. 2. Watch the edit with inserts, intro, subtitles and sound.
3. Drag an insert block on the timeline to move it, drag its edges to change the duration.
4. Drag the zoom target point.
**Settings:** the preview uses the same scene plan that goes into the build — the plan is
served by `/api/scene` without GPU work, and every style edit comes back as a new plan, so
the subtitles change at once, on a paused player too. When the style has roto or head
tracking on, a **Compute roto and tracking** button sits at the top of the words/intro tab:
it computes the RVM masks and the head track on the GPU with per-chunk progress and a
**Stop** button, marks itself "computed" when done, and the preview then shows the speaker's
cut-out figure above the intro in the style's layer order, in sync with the player; head
tracking appears from the plan right after the calculation. The calculation takes the shared
job lock, so it does not run on top of a cut, a build or a render, and the masks it computes
are reused by the build. The subtitles do not drag with the
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
`api/previewproxy.py:66`, `api/files.py:261`, `static/app/60-preview.js:113`

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
one master script and one `aerender` run; a single clip keeps the plain path. **How many AE
copies build the project** is set in ⚙ › **Tools** › **After Effects**
(`ae_build_workers`, "auto" by default): a set is built by several `AfterFX` copies in
parallel, each taking its own share of the clips, and one more copy then merges the parts
into a single project; "auto" picks min(3, the number of clips, free memory / 10 GB), and
"1" keeps the old path. `aerender` is launched with the memory limit `-mem_usage 40 60` as
well, otherwise After Effects takes almost all memory, Windows goes into swap and the render
is slower (361 s against 290 s in one measurement). During the
**AE project build** stage of a multi-clip set, the progress display shows "N of M", which
clip is currently building, and the ETA, while already built clips in the queue show "built,
waiting for render". Batch phase timings from previous runs are remembered and make the ETA
better over time.
**Limitations / price:** After Effects must be closed: an open copy would swallow the
headless run, so the job refuses to start. There is a stall watchdog, and an instant
AfterFX exit is reported as a likely open AE copy. Rotoscoping runs during the build and is
the longest stage.
**Code:** `core/render_job.py:1483`, `core/aerender.py:34`, `core/render_job.py:1289`,
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
**Code:** `static/app/55-progress.js:276`, `templates/index.html:530`,
`templates/index.html:553`, `core/umsg.py:1`, `core/jobstate.py:204`, `api/jobs.py:32`,
`static/app/00-core.js:77`

## Settings and tools

### Interface language

**Where:** the language button in the header.
**How:** Press it. The page reloads in the other language.
**Settings:** the choice is remembered; the initial language follows the OS language.
Russian is the source language, so a missing translation simply stays Russian.
**Code:** `templates/index.html:24`, `static/app/00-core.js:125`,
`static/app/00-core.js:152`, `tools/i18n_extract.py:1`

### Where models are computed: video card or processor

**Where:** ⚙ → "Cut", the "Where models are computed" field — next to "Clips at once".
**How:** pick "Auto" (the card if there is one, otherwise the processor), "Video card" or
"Processor". Under "Clips at once" a grey line shows what was chosen and how many
recognitions run at once: "models on this machine: video card, slots 4".
**What it changes:** recognition for cutting, breaths and emotions are computed by the model
service — one process per machine that keeps the weights in memory and counts in parallel, by
slots. On the processor it takes no video memory at all: that is how a machine without a card
works and how the card is freed for a local LLM (LM Studio, Ollama). On the card there are
more slots, but the card is occupied.
**Settings:** stored on the server (`ai_config.json`, field `model_device`), not in the
browser: the service is a separate process. Changing it restarts the service on the next
request (the old one is stopped, a new one starts — already on the other device).
**Code:** `templates/index.html:868`, `static/app/10-settings.js:216`,
`core/model_service.py:1`, `core/device.py:39`, `api/model_svc.py:1`

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
