# Known defects

Open bugs that nobody has got round to. The list is deliberately short: only what
reproduces and gets in the way goes here, not everything in a row. Fixed — struck out of
here the same day, like the implemented plans.

There is one format: **the symptom in the user's words** → what is known about it → what has NOT been checked.
The symptom matters more than the hypothesis: a hypothesis is to be tested, a symptom is to be reproduced.

Below the list there is a separate "Technical debt" section: not defects caught live, but the things
that make the next edit cost more. Its format is the same.

---

## Premiere export (FCP7 XML): black screen on stop, a picture while scrubbing

**Symptom (user, 2026-08-21).** The XML imports, the project opens, the clips are on
the timeline. But the monitor shows a **black screen**: while you drag the playhead the picture is visible,
you let go — black again.

**What is known.** The assembled XML passes the internal checks, the files are in place (otherwise
`core/verify_jsx.py` and the import would complain about missing paths). That is, it is not the project's structure
that breaks, but the playback.

**What has NOT been checked.** Nothing: debugging has not started. The first thing to look at when there is
time — the **time base and frame rate**: `<rate><timebase>` and the `<ntsc>` flag in the assembled
XML against the real rate of the sources (23.976 against 24, 29.97 against 30). The classic
cause of this symptom in Premiere is a mismatch between the sequence settings and the media. **This is a hypothesis,
not a diagnosis**: nobody has checked it.

**Done (2026-09-25).** The source file in the XML is written with the real frame rate from `probe()`
instead of hardcoded 29.97 DF (timebase, the ntsc flag, displayformat and the timecode separator).
There is no confirmation in Premiere yet — do not delete the section.

---

## DaVinci Resolve export (.drp): the camera frame is not applied

**Symptom (predicted from the code on 2026-08-24, NOT observed live — nobody has looked at the frame in
Resolve).** A video assembled in Resolve shows the camera source with a centre crop,
although the speaker has a frame set (`frame` in the profile): in After Effects, in Premiere and in
the preview the frame is the same, but here it is not there.

**What is known.** The `.drp` builder (`core/drp.py`) does not write a clip transform AT ALL: neither
scale, nor shift, nor crop — neither for cameras nor for inserts. A clip carries its own resolution
(`<Geometry>` from the probe), and Resolve does the layout itself. In the sample clip of the template there are no explicit
transform parameters either (names like Zoom/Crop/Pan do not occur in the node markup;
the opaque blobs `EffectFiltersBA`/`UiMemento`/`FieldsBlob` have not been figured out), and inventing
the format blindly is a sure way to get a project that will not open (this already happened with
`MediaTimemapBA`). That is why the frame is not carried into `.drp`: exactly what has been verified was done —
XML for Premiere and `.jsx` for AE.

**What has NOT been checked.** Nothing: nobody has looked inside Resolve itself even once. When there is
time — dump a project in Resolve with one clip shifted and enlarged by hand and see how
that transform is written in the `.drp` (the crop is there too — the node's `Crop`/`Zoom`/`Pan` in the
Inspector), and only after that write it.

---

## DaVinci Resolve export (.drp): the timings are wrong

**Symptom (user, 2026-08-21).** The project imports and opens, but
**the timings look wrong** — as if the clips are not where they should be.

**What is known.** The `.drp` format has been figured out byte by byte (`docs/DRP_SPEC.md`), the export is assembled from
the depersonalised template and used to open before. The numbers come into the scene plan the same as those from which the `.jsx`
for After Effects is assembled, and the AE branch shows no discrepancies — so most likely
the matter is in the conversion of these numbers into Resolve's units, not in the numbers themselves.

**What has NOT been checked.** Nothing. It is worth starting with the same frame rate: two different
exports break at the same time and both in time — this hints at a common cause further up
the stream, not at two independent bugs.

---

## DaVinci Resolve export (.drp): the processed voice of camera 1 has not been checked live

**Symptom (predicted from the code on 2026-09-26, NOT observed live — nobody has opened a `.drp` with a voice in
Resolve).** For a speaker with voice processing on (the AI denoiser or a chain plug-in), the processed voice lies next to
the XML, but in Resolve the raw camera sound may play — or the camera 1 record may go offline.

**What is known.** The audio descriptor of the camera 1 record (`<Clip>` inside `<BtAudioInfo>` and its
`<TracksBA>`) is rewritten to `<stem>.voice.wav`: the path, name, mtime, the PCM codec, while
`SampleRate`/`NumChannels`/`Duration` are taken from the WAV itself (`core/drp.py`, `set_voice_descriptor`,
the analysis — in `docs/DRP_SPEC.md`). The tests confirm: only the camera 1 record is replaced, the video and
the timeline clips do not change, `MediaRef` stays equal to the `DbId` of the `<BtAudioInfo>` element, and without the
file `.drp` is byte-for-byte the previous one. Not a single field is invented: the record's `<AudioSource>` remains
the template `AUDIO_SOURCE_EMBEDDED` — there is no `.drp` reference with sound from an external recorder, and what
Resolve writes in this field when attaching a separate file has not been captured.

**What has NOT been checked.** Resolve itself: a `.drp` with a voice has not been imported there (nor launched).
When there is time — import a project with a voice and check whether the processed sound plays and whether
the camera 1 record went offline. If it does not work — take a reference: attach the sound from an
external recorder in Resolve by hand, export the `.drp` and look at `<AudioSource>` and the
other attachment fields.

---

## macOS: the insert library duplicates files whose path differs in case

**Symptom (predicted from the code on 2026-08-23, NOT observed live — we have no Mac users
yet).** On a Mac the same file lands in the insert library twice if it is recorded in the index
in a different case: duplicates in the selection output, extra `gone` marks, the `used` counter
smeared over two records.

**What is known.** The path key in the index is computed through `os.path.normcase`
(`core/insertlib.py`, `build_index` and two dozen more places; plus four in `api/videogen.py`).
This function looks at the PLATFORM, not at the file system:

| OS | `normcase` | the FS in reality | Matched |
|---|---|---|---|
| Windows | lowercases | case-insensitive | yes |
| Linux | does nothing | case-sensitive | yes |
| macOS | does nothing | APFS by default case-INSENSITIVE | **no** |

That is, on a Mac `Foo.png` and `foo.png` are one file on disk, but two records in the index.
The same discrepancy explains the skipping of two tests on the Linux runner
(`tests/test_insertlib_speed.py`, the marker `insensitive_fs`): there the behaviour is exactly right.

**Done.** Ask not the platform but the FS itself: `paths.pkey(path)`
checks with a `samefile` probe that the swapcase variant of the path exists and refers to the same
file; the result is cached by the ancestor directory. All path keys in `core/insertlib.py`,
`api/videogen.py` and `core/roto.py` have been converted to `paths.pkey`.

**What has NOT been checked.** Nothing on a real Mac: neither case-insensitive APFS, nor case-sensitive
APFS (installed by hand), nor external volumes with exFAT/NTFS, where the rule is its own rather than the
system one. The `samefile` probe was run in tests with a simulation — it has not been checked on a real Mac.

---

## The code cut cleanup throws away a real word before a cognate one

**Symptom.** With "Code cut cleanup" on, in "спорт спортивные" or "свет
светодиодной" the first word disappears.

**What is known.** `drop_truncated` (`core/gigaam_cut/takes.py`) considers any kept word of 4+ letters that is a strict
prefix of the next one a stub. A fragment cannot be told from a real word by length alone. With the default
"Code cut cleanup" = off, this cleaning does not work in ordinary cutting.

**What has NOT been checked.** How to tell a stub (duration, probability, the pause before
the retake) — a comparison with the owner's edit reference is needed (`user_overrides`); without it any
rule is guesswork.

---

## The live voice host always opens two channels

**Symptom (predicted from the code on 2026-10-02, NOT observed live — nobody has checked mono devices
with us).** The voice track in the preview goes to the output device with two channels
(`core/voicefx_editor.py`, `CHANNELS = 2`): the stream opens with `num_output_channels=2`, and
mono sound is duplicated into both channels. On a mono device or a mono driver (a laptop's built-in
speaker in mono mode, some USB headsets, an ASIO driver with one output) the stream
may not open at all — then the host sends `audio_error`, the page takes the sound back,
and the live plug-in sound does not work.

**What is known.** The channels are forced to two deliberately: camera 1's voice is sometimes mono, and
the stream needs the same channel count, while stereo plug-ins (ValhallaVintageVerb and the like) crashed
with "return code 1" on a mono buffer. The channel count is the constant `CHANNELS`, not a query to
the device: `pedalboard.io.AudioStream` can return the `num_output_channels` of an open
stream, but that branch is not in the host.

Since 2026-10-06 the live host comes up ONLY while a plug-in window is open (windows closed —
all steps play the baked track). So a host audio failure costs exactly the time
while a person turns the knobs, and the preview does not see it in ordinary work.

**What has NOT been checked.** Not a single mono device: neither opening the stream nor what
`StreamResampler(SR, out_rate, 2)` does with a mono output. When there is time — open the
preview of a clip with a speaker with VST on a mono device and see whether the sound arrives; if not —
ask the device for its channel count and duplicate/sum by it.

---

## The preview has no 10-ms fades at the clip seams

**Symptom (from the code, 2026-10-02).** In After Effects and in the built-in render without AE the edges of the
camera audio clips have a micro-fade of ~10 ms (the style `audio_fades`, in `.jsx` — `AUDIO_FADE`,
in `core/webrender_audio.py` — `afade` at the edges of a piece), and no clicks are heard at the joins. The browser
preview has no such fades: the cameras' `<video>` plays the pieces as they are.

**What is known.** The reason is that the preview plays the camera SOURCE under the playhead, not
the cut stream: a fade at a seam is a property of the build, not of the file itself. The processed voice
(the `vt*` track) in the preview also plays the `.voice.wav` file without fades. By ear the defect is noticeable
at joins in the middle of a loud word; in most clips the seams fall in silence, so
there were no complaints.

**What has NOT been checked.** How audible this is on real material and whether it is even worth
repeating the fade in the preview: this needs a Web Audio node with a ramp at every seam, and
the playback strategy in the browser is different (one `<video>` per camera, without cutting).
There are no "before/after" measurements.

---

## The yellow strength computation reads the sound windows with a separate ffmpeg each

**Symptom (technical debt, 2026-10-03).** The strength of yellows (`core/emphasis.py`) reads the sound
in WINDOWS around the needed words, not the whole source — this has already saved the computation from memory growth
with the recording's length. But each window is read by its own call of `_read_window` →
`core.sync.extract_audio` (ffmpeg `-ss`/`-t`): overlapping windows are merged
(`_merge_windows`), but separate ones still give one ffmpeg process per window.

**What is known.** On a clip with fifty yellows that is dozens of ffmpeg runs; on a long
recording with rare yellows the windows are far apart, and merging does not help. Pitch and RMS are
computed once per window (`tone_track`), not per word — the debt here is exactly in reading the sound.

**What has NOT been checked.** Whether it is worth reading all windows in one ffmpeg pass (a concat filter or
one `-ss`/`-to` per range with subsequent slicing of the array) and how much this would save:
no measurement was made, and the gain depends on the density of yellows in the clip.

---

## More than two cameras have not been checked

**Symptom.** Not a defect but a verification boundary: the pipeline is designed for several cameras (in the
interface and in the CLI you choose from one to four), but was run live on two.

**What is known.** The camera count is a parameter, not a constant: `build_queue(base, n_cams)` and
`process_pair(cams, ...)` in `core/cutjob.py` accept a list of cameras (the docstring promises 1..4),
`core/align.py` lays the segments out over the cameras, and the XML and `.jsx` are assembled by the number of inputs.
That is, the code is designed for three or four cameras, but has been verified on exactly two — the reference was taken on them,
and on them the preview matches the build.

**What has NOT been checked.** Not a single live project with 3 or 4 cameras: whether the
audio sync holds (pairwise offsets against the reference camera), what the layout looks like with
three or four windows, how the switches between cameras behave on long material and how much
such a run costs in time and video memory. The README promises only what has been verified:
support is designed for several cameras and verified on two.

---

## SSRF: downloading a video by a provider-supplied address — closed

**Symptom.** None — it is a finding of an external review, analysed from the code: the scenario was not reproduced
live.

**What is known.** There was one place — downloading the finished video: `core/aicut/video.py`, a loop over
`tries` with `urllib.request.urlopen(req, timeout=600)`. The addresses are taken from `urls` in the provider's
response and from `content_ep`, that is, they are chosen by an external service, not the user: the scheme and
the host were not checked, `urllib`'s redirects passed silently, and an honest provider link could
take the request to `127.0.0.1` or into the local network. The hole was NOT `base_url` from
`ai_config.json` — it is chosen by the user, and `http://127.0.0.1:1234` (LM Studio) is a normal
address there. Images are taken only from `base64` in the response (`core/aicut/images.py`); the code does not
follow the links it is sent.

**Done.** Before downloading, the scheme (only `http` and `https`) and
EVERY address the name resolves to are checked: loopback, link-local, private, reserved,
multicast and undefined — a refusal (`unsafe_url_reason`, `core/app_meta.py`). Redirects go
through a handler of our own with the same check and do not carry `Authorization`,
`Proxy-Authorization` and `Cookie` to a foreign host (`SafeRedirectHandler`, same place). A refusal is a reason not
to download, not a crash: the retry loop takes the next address, the paid result is not lost.

**What has NOT been checked.** The scenario was not reproduced live: both the finding and the closing were done from
the code and the tests (`tests/test_ssrf_download.py`), not by a run with a hostile provider.
The behaviour of `rclone` when downloading from Google Drive has still not been checked.

---

## Priority

**Low, and that is the user's decision.** The main pipeline ends in After Effects, and
it works: the preview matches the build, `aerender` runs headless. Premiere and Resolve are
**fellow travellers, not the goal**: XML is needed for those who edit in Premiere, `.drp` — for those
who have no Adobe (Linux first of all). While nobody has taken them up seriously, it is more honest
to say in the README that one path is verified than to pretend that all three are equal.

---

## Technical debt

Not defects but debt: today it does not go off, but it determines how much the next edit costs.
Below is what remained open; the numbers were counted from the code on 2026-09-23, and the method of counting is named in
the item itself. What was closed after the external review of 2026-09-22 — one line per item at the end of
the section.

### Preview sound: the graph and the Firefox limitation

**Symptom (a live run on Linux).** "The video in the preview plays, there is no sound", and the cause
turned out to be twofold: (a) the camera sound was brought into Web Audio unconditionally
(`createMediaElementSource`), and a suspended `AudioContext` mutes this path entirely and
silently; (b) the camera sources write `pcm_s16be` into MP4, and Firefox does not decode such sound
at all — until the video proxy is built, it plays the silent source.

**What is known.** Cause (a) was closed on 2026-10-07: starting playback wakes the graph through one
door (`audioWake`), and the camera goes into the graph lazily — only when the processed
voice or the live host is sounding. Cause (b) is a BROWSER LIMITATION, not a defect of the project: Chromium
reads the sound of the camera sources (a measurement on 2026-10-07: Chrome 152 on Windows and Linux), Firefox
does not. While the preview plays the source, the player says so in a line based on a fact
(`<video>.mozHasAudio === false` after 1.5 s of playback; the property exists only in Firefox);
it waits for the video proxy — its track is already aac, and the line goes away by itself. The recommended browser
is Chromium-based (Chrome, Edge, Brave). The guard — `tests/test_preview_audio.py`.

**What has NOT been checked.** Nobody listened live in Firefox: the line is checked by stands that substitute
`<video>` and the DOM; a real Firefox on the owner's material has not been run. Nobody has looked at how the sound
behaves on a very long clip. The live plug-in host on a mono device is still
not checked (see the section above).

### `scene_plan` — 885 lines

**Symptom.** One function assembles the whole scene: to fix any part of it, you have to read
it entirely.

**What is known.** The function was split into nine `plan_*` modules (subtitles and highlights, intro,
intro data for the template, inserts, music and transitions, camera, word preparation, asset folders and
fonts, frame decoration — the plate, the progress bar, the caption, the disclaimer), and at the same time the overflow
of style parameters went away: the style is read ONCE into a structure (`core/xml2ae/plan_style.py`,
`read_style`), and the blocks then take ready values. Currently `scene_plan`
(`core/xml2ae/build.py`, lines 393–1277) is 885 lines, down from 1213; the lines starting with
`if`/`elif`/`for`/`while`/`except` number 28, down from 60 (the same count over the function's text). Each extraction was
verified by a byte-for-byte comparison of the assembled `.jsx` and the plan on real videos.

**What has NOT been checked.** It was not split further: the remainder is the assembly of the template substitutions, and the cost of
the next step has not been measured.

### Routes and functions without tests

**Symptom.** An edit in an uncovered place is verified by hand only, and not everything is verified by hand.

**What is known.** Routes: in `api/` there are 88 `@bp.route` declarations. A measurement by `tools/route_coverage.py`
showed that before the check, calls through the test client existed for 75 routes and for 13 there were none
(the routes were mentioned only in strings and comments). Now all 88 routes are covered by real
behaviour calls (response format, refusal on invalid input, side effects), and the guard
`tests/test_route_coverage.py` holds the requirement: a new route without a call is red. `core/omni_cut.py`: 27 functions; five
private helpers are not named in the tests (`_norm_phrase`, `_rms_env`, `_wordset`,
`_wav_duration`, `_frange`) — they are called from covered code; `main()` is an integration orchestrator and
has no unit tests. `core/omni_asr.py`: all the decision functions are covered without the network, models and
the video card (`tests/test_omni_asr_units.py`); the two defects the tests found — a non-JSON provider response
bypassing the retries and an undeletable temporary wav — have been fixed.
Line coverage (pytest-cov, `api/`, `core/`, `tools/`) — 78.5 %; in CI the `test` job fails below
77 %. It was 74 %: tests were added for `core/asr_backends.py` (47 → 95 %), `core/ctc_asr.py` (0 → 98 %),
`core/cutjob.py` (43 → 100 %) and the pure logic of `tools/` (7 → 32 %). The tests found two defects — both
fixed: word alignment was silently discarded (`emit(..., line=...)` raised a TypeError) and the counter
of empty accents in `tools/intro_rules.py` was overstated by 1.

**What has NOT been checked.** The uncovered functions have not been examined one by one: which of them are dangerous and which are
thin wrappers was not looked at.

The `tools/` scripts whose `main()` is deliberately without tests (the pure helpers are covered):
- `tools/webui_test.py`: brings up a debug HTTP server on port 5098 and mutates the profile's environment files.
- `tools/bench_vision.py`: measuring models requires a running LM Studio server and a GPU (the pure functions `_keyword`, `hit`, `select_oracle` are covered).
- `tools/train_breath.py`: training the boosting requires scikit-learn, the GigaAM models and audio (the pure functions `_rate`, `_clips` are covered).
- `tools/harvest_good.py`: `main()` is tied to the user's files and the references in `data/` (the pure logic `_center_xy`, `resolve_files` is covered).
- `tools/intro_rules.py` and `tools/intro_hook_rules.py`: their `main()` are tied to the user's real folders `MKAutoCut_out/` and `AutoCut_out/` (the pure logic is covered).


### Closed after the 2026-09-22 review

- **`SystemExit` as an error channel** — closed: user errors go through
  `core.umsg.ReelsiError(Exception)`, and before every `except Exception` whose body calls the project's
  code there is `except ReelsiError: raise` (380 such places; another 24 handlers catch it as
  `e` to show the text). All 22 entry points `if __name__ == "__main__"` have a catch;
  the guard — `tests/test_reelsi_error.py`. Integer process exits and `sys.exit` in `main()`
  remain deliberately — that is legitimate.
- **Swallowed errors** — closed: all 151 handlers have been analysed (26 hid a refusal — now
  they write to the log, 125 have a "why" comment); currently the guard sees 126 handlers whose body is
  `pass`, and each has an explanation. Data losses have been fixed: learned phrases, the media library
  transfer log `_import_log.json`, the video generation history and the glossary — a broken file
  is set aside next to it in `.bad-<time>` (`core/fileio.py`, `quarantine_unreadable`) instead of
  being overwritten. The guard — `tests/test_no_silent_except.py`.
- **`.project.json`** — closed: one writer and reader, `core/project_file.py`, format version
  1 (a file without `version` is read as version 0); the guard —
  `tests/test_typing_ratchet.py`.
- **Render orchestration in an HTTP module** — closed: launching AfterFX and `aerender`, the idle guards,
  the master project and the batch render — in `core/render_job.py` (`RenderJob`), the state of
  jobs without Flask — in `core/jobstate.py`; `api/render.py` — 162 lines: the job instance,
  "Stop" and two routes. The guards in `tests/test_layers.py`: `core/render_job.py` and `core/jobstate.py` without
  `flask` and `api`, and `api/render.py` has no `Popen` and no functions longer than 60 lines.
- **Typing** — closed: strict `mypy` (`disallow_untyped_defs`) is on for all 122
  code modules — `api/`, `core/`, `tools/` and the root ones; the return annotation is present on 1284 functions out of
  1286 (two `__init__` without `-> None`, which is allowed). It is also the "Type check" step in CI; the ratchet —
  `tests/test_typing_ratchet.py`. The edit was in the types only: the AST without annotations matches the
  previous one for every file; `# type: ignore` — 28 for the whole code, each with an error code and a reason.
  A `.pre-commit-config.yaml` has been added to the repository with local hooks for `ruff`, `mypy`, the task-code guard and a check for mixed line endings (`tools/check_eol.py`).
- **Layer inversion** — closed: `api/` does not import the CLI, `core/` does not import `api` and
  `flask`; the guards `tests/test_layers.py`, `tests/test_api_no_cli.py`.
- **Sidecars next to a supplied XML/media (`.project.json`, `.words.json`, `.cuts.json`, `.yellow.json`, `.caption.json`, `.breaths.json`, `.omni.json`)** — closed: derived paths are checked by a single function `sidecar_path` (`api/_core.py`) against a denylist of secrets (`_never_serve`) and leading references (a symlink/hard link outside the base file's directory — a `ReelsiError` refusal). All 14 places in `api/` have been converted to it; the guard — `tests/test_sidecar_guard.py`.


### Closed 2026-10-06

- **The clip's final voice was replaced in place under a permanent name** — closed:
  `<stem>.voice.wav` was rewritten over itself, and on Windows the replacement failed if the file
  was held open by the preview player (it plays exactly this file), by the `/api/media` serving or an
  open AE/Premiere project: the new voice did not appear, and the exception went as one
  line into the log, and the project left with the camera sound — "on step 1 the voice was processed, in
  the video it is not". Now the track is written under a versioned name
  (`<stem>.voice.<key8>.wav`, `key8` — the first 8 characters of the processing cache key), the version name
  lies in the sidecar `<stem>.voice.json` as the `"file"` field, and the readers take the path
  with the resolver `final_voice_path` (the name from the sidecar, otherwise the old `<stem>.voice.wav` —
  clips baked before that are read as they were read). At the same time what lived nearby was closed:
  two counts of one clip at once (a lock by `realpath(xml)` — the second order gets the
  ready track), the preview plays an immutable copy in the cache rather than the file next to the XML, and
  a bake failure is visible — a toast and the line "voice without processing" in the preview, a warning
  in the build result. The guard — `tests/test_voice_final_file.py`.
- **The live plug-in host always played and lagged the preview** — closed: with VST on, the step 1 preview
  lagged ("it did not finish the ends, sometimes it played more and in the wrong place"), because the sound went
  through a separate host process and was adjusted by seeking with a tolerance of 0.4 s. Decided by the
  owner: the plug-in window is open — the sound goes live through the plug-ins (turn and listen);
  the windows are closed — the settings of ALL plug-ins are saved, the voice is re-baked, and all steps play
  the baked track. Closing the window and shutting down the host begin with the `dump_states` command:
  the host returns the state of each loaded plug-in (the `states` event), the server writes them
  into the speaker profile by plug-in PATH in one save, and only then is the process taken down by
  PID. While the voice is being re-baked, the PREVIOUS baked track plays (not the camera sound).
  The sync of its own track became stricter: a discrepancy of 0.03–0.25 s is killed by SPEED
  (`playbackRate` ±6 %, as with the cameras), more than 0.25 s — by seeking, and on a pause and a jump over
  a cut the position is set exactly and at once. The host's input is the denoise track, if it has already been
  computed (the raw sound remains only for those with nothing else to play).
  The guard — `tests/test_voice_preview_mode.py`.


### Closed 2026-10-07

- **Cutting and subtitles did not share the video card and were not checked against its size** — closed:
  the set number of videos (1..16) was not checked against the free video memory, and each
  video process holds ITS OWN CUDA context of ~568 MiB (a measurement on a 4096 MiB card: four
  processes = 2307 MiB, a peak under work of 696 MiB) — ten videos on 4 GB is
  an `OutOfMemoryError` inside the subprocess and "code 1" in the interface without a reason.
  `cut_parallel_width` cuts the width by `free_vram // 600 MiB` (`core/device.py`, the same measure
  as in `doctor.py`: torch, then `nvidia-smi`) and writes the reason into the job log
  ("the card is 4.0 GiB, each video holds ~600 MiB — no more than 6 at once (10 were requested)");
  on a machine without CUDA there is no ceiling. The subtitle step (`POST /api/gen_subs`) brought up
  the ASR in the server process and took no lock at all — subtitles on top of an ongoing cut mean
  two models on one card; recognition now goes under `gpu_lock("субтитры")`.
  A model that did not fit no longer brings the step down with the raw text
  `CUDA failed with error out of memory`: the attempt is repeated in `int8_float16`, then smaller
  (large-v3 → medium → small), each stage names the card size and what was taken
  instead of it, and when nothing fit — the response has the code `whisper_gpu_fallback` with a
  translation. Out-of-memory exhaustion is recognised by the type or the text of the exception in one
  function (`core.device.is_oom_error`). The guard — `tests/test_gpu_budget.py`.
