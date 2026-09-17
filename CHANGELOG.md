# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added
- **Background crashes leave traces**: a native crash of the web UI server writes its traceback to `reelsi_crash.log` next to the log, unhandled exceptions in background threads go to `reelsi.log`, and the next start records that the previous run ended abnormally, with the crash log tail and the matching Windows Application log entries.
- **Style panel in the spirit of Effect Controls**: the style panel is built from one schema (`core/style_schema.py`: layer → group → field, every key of the base style) served by `/api/style_schema`; rows are compact, groups fold, numbers can be dragged with the mouse, layers and effects have their own toggles, each group has a Reset button, and a dot marks changed values up the tree. The hand-written bindings for about 120 style keys in four places are gone; a guard test keeps the schema and the base style in sync.
- **Intro fade-out**: intro words fade out over 0.35 s with one style key, `intro_fade`, shared by regular and glitch intro precomps. The former glitch-only keys `intro_fx_fade` and `intro_fx_fade_last` are removed from saved presets when they load, so a custom value there falls back to the new 0.35 s; set `intro_fade` instead. The default After Effects build changed on purpose, and the geometry golden file was updated with it.
- **Intro dimming**: the style option `intro_shade` with an opacity slider (`intro_shade_op`) adds a blurred dark layer at the bottom of the frame for the whole video, stacked under the intro and above the camera clips (as in the hand-made projects it was measured on); the preview shows the same dimming. Its size follows the composition width (the numbers are measured on 1080 px wide projects) and `intro_scale`.
- **Subtitles during photo inserts**: the style key `insert_sub_swap` (on by default) moves subtitles away while a rising photo insert is on screen; turn it off to keep them visible.
- **Feature guide**: `docs/FEATURES.md` and `docs/FEATURES.ru.md` describe every feature step by step: where it is, how to use it, settings, limits and cost.
- **Glitch glow choice**: yellow intro words with the glitch animation glow with the built-in Gaussian Blur and Glow or with the third-party Deep Glow 2 plugin (⚙ › Tools › After Effects).
- **Build progress for a multi-clip set**: the AE project build stage shows "N of M", the clip being built and the ETA; built clips are marked "built, waiting for render".
- **Package metadata**: `pyproject.toml` declares the name, the version and `requires-python >= 3.10`.
- **Install from a clone**: `pip install -e .` installs dependencies from `requirements*.txt` and registers `reelsi`, `reelsi-webui` and `reelsi-doctor`; a plain `pip install .` is not supported and the commands say so.
- **`--forced-align` in the CLI**: `reelsi.py` accepts the flag the web job already passed.
- **API route tests**: contract tests cover 19 more routes (camera loading and swap, `.drp` export, editor state, insert library, style presets and censor word lists, AI inserts, temp info, video probe): bad input, the response contract and that writing routes change only what they promise.
- **Log file**: the web UI writes a rotating log (`reelsi.log`, or the path in `REELSI_LOG`) with the startup line and the causes of failures that used to be swallowed silently.

### Changed
- **Tests never touch personal files**: every state path (AI call log, UI state, job lock, video history, word lists, insert index, AI config) points to a test folder before the app modules load, the AI config starts empty, and a guard fails the run if any top-level file of the repository changes. Cutting thresholds are restored after each test.
- **Style panel tests catch real breakage**: the DOM stub only knows the elements the panel created; visibility, field read-back for every control type, the slider row and the roto fraction are checked by behaviour, and each check was confirmed by a deliberate code mutation.
- **Every style knob is checked end to end**: each of the 118 style keys changes the built `.jsx` (caption, intro rows, sound, music, transition and roto included), fractional fields keep their scale, and a full pass through the style panel leaves every value unchanged.
- **Style panel layout**: layer order rows are 24 px high like the other rows, and sliders take the full width.
- **Roto failures stop the build**: when roto is on in the style (it is on in the base style) and a chunk gets no mask — out of video memory, a model error, an empty mask, or no PyTorch — the build now fails with "roto was not computed for N of M chunks" or "roto failed" instead of silently producing a project without roto. Computed masks stay cached for the next build; turn roto off in the style to build without it.
- **Roto mask cache key**: masks are keyed by the normalized path, the nanosecond modification time and the file size, so a camera file overwritten in the same second no longer reuses an old mask. Existing cached masks are recomputed once.
- **Style defaults in one place**: every fallback value the After Effects build uses for a missing style key now comes from `styles.BASE`; a guard test stops new hard-coded fallbacks.
- **Style presets**: keys the build no longer reads (`roto_video`, `caption_padx`, `caption_pady`, `intro_fx_fade`, `intro_fx_fade_last`) are removed from saved presets when they load.
- **Golden files**: the "default build stays byte-identical" rule now states its exception — a deliberate change of the default build updates the golden file in the same commit and is recorded here.
- **CLA**: removed the internal author note from `docs/CLA.md`; `docs/CLA.md` and `.github/CONTRIBUTING.md` link CLA Assistant, which checks pull requests.
- **Contributing**: `.github/CONTRIBUTING.md` explains the task codes («задание GZ») found in comments and specs.
- **Demo**: the README demo is an animated WebP, 3.8 MB instead of the 8.4 MB GIF.
- **CI**: tests that need Pillow, pyarrow and zstandard run instead of being skipped; the workflow token is read-only; Python 3.12 and 3.13 byte-compile the code and check `--help` of the command-line entry points.
- **Security policy**: `.github/SECURITY.md` states that the breath detector loads its model with `trust_remote_code=True` and how to run without it.
- **Pinned downloads**: whisper.cpp is fetched as `v1.9.2` with a SHA-256 check and archive path validation; Robust Video Matting is loaded from a fixed commit and CED-tiny from a fixed revision.
- **Docs**: `docs/ARCHITECTURE.en.md` describes `ui_state` as localStorage with a server-side mirror, as the code does; both architecture docs note why the translation dictionary is always inlined; README says the fully verified path is After Effects.
- **Interface language**: style names, the camera folder placeholder and the sound effect caption are translated.
- **Removed**: the unused per-clip batch render path (`_run_render_batch`, `build_render_batch`).
- **Subtitles**: one function builds the Premiere subtitle track, so long words are font-scaled on every route; one rule (word midpoint) places words on the timeline for both the web route and the CLI. Two SRT writers remain on purpose: each mirrors the rows of its own output (After Effects scene rows, Premiere word grouping).
- **Preview proxy and GPU**: building camera proxies takes the shared GPU job lock, so it no longer runs on top of a cut or render.
- **Media endpoint**: `/api/media` serves only video, image and audio files; any other extension is refused, so copies of config files can no longer be read through it.
- **Cross-site requests**: a request with `Sec-Fetch-Site: cross-site` or `same-site` is refused for every method, not only for state-changing ones; request bodies are limited to 32 MB.
- **Fonts on macOS and Linux**: font lookup also searches the standard macOS and Linux font folders, including subfolders.
- **CI on Windows**: the test suite also runs on `windows-latest`.
- **GigaAM pinned**: the optional GigaAM dependency is installed from a fixed commit.

### Fixed
- **"Nothing to cut" needs the whole text back**: when the model marks nothing for removal, at least 90% of the video's words must be in its answer; half a transcript without brackets (a cut-off answer) is an error, not a finished cut.
- **A normal stop is not reported as a crash**: stopping the server with SIGTERM (SIGBREAK on Windows) removes its run marker, so the next start no longer warns about an abnormal end; the warning now says "crashed or was killed".
- **Style panel controls have accessible names**: sliders, hidden number inputs, colour swatches, file and reset buttons and tree rows are labelled; the layer order list is labelled by its caption.
- **A model answer about some other text stops the cut**: when fewer than half of the video's words are found in the model's answer (an empty answer, a refusal, text from another video), the cut stops with an error instead of reporting "nothing to cut" and overwriting the timeline with one piece. The older VAD path checks the model's decision before long intervals are restored.
- **Secrets are not served through hard links**: the secret-file guard also compares the requested file with the known secret files, so a hard link with a media name no longer serves a key.
- **Style panel works from the keyboard**: numbers are focusable spin buttons (arrow keys change them, Enter edits, Escape cancels), expand arrows and reset dots are buttons with labels, field labels point at real controls, and focus is visible.
- **Cutting no longer reports a wiped-out cut as success**: when the model rejects almost all speech (or returns nothing, or text from another video), the cut stops with an error before the code cleanup runs; the code cleanup used to bring the whole range back, so an empty cut was logged as done. The previous cut stays untouched.
- **One default for the code cleanup of the cut**: "Fix the cut with code" is off by default everywhere — the stage, the interface, the speaker defaults and the command line; a speaker profile or the checkbox still turns it on. Loading no speaker profile resets the thresholds to their defaults.
- **Roto mask bottom is a fraction again**: opening a clip wrote the shown percentage (35) into the style instead of 0.35, and the build clamped it to a full-frame mask. The build now treats a stored value above 1 as a percentage, so an already affected state is repaired.
- **Style panel**: the slider row of a dependent field comes back with the field; the "editing a template, Save will overwrite it" note is shown again; the zoom point highlight works for the button the panel creates after loading; layer order has a label, a changed marker and a reset; a failed schema load is retried the next time the panel opens; the style label is stored untranslated.
- **Files are checked by their real path**: the secret-file guard and the extension checks of the media and XML export routes also look at where a symbolic link points, so a link with an allowed name no longer serves a key file or a non-media file.
- **Draft proxies survive cleanup**: the automatic cleanup before a cut keeps the 720p draft proxies (`proxy_*.mp4`) as well as the preview proxies.
- **doctor and whisper.cpp**: the whisper.cpp check runs even without PyTorch (the machines that need whisper.cpp most often have no CUDA build), lists the downloaded ggml models and warns when there are none; running doctor twice in one process no longer adds up the problems.
- **Whisper frees video memory on failure**: the model is released even when transcription raises, so a failed run no longer keeps it in VRAM.
- **Video model catalog follows the provider**: the catalog in memory is tied to the provider and address it came from; after switching the video profile it is fetched again, so a model is no longer rejected or its fields cut by another provider's list.
- **Paths on case-insensitive disks**: insert library, video history and roto mask keys ignore letter case where the file system does (macOS APFS by default), not only on Windows, so `Foo.png` and `foo.png` are one entry; keys on Windows and case-sensitive Linux are unchanged. Not tested on a real Mac.
- **Insert library cache between processes**: the index is re-read when its nanosecond timestamp or size changes, so a write by the command line and the web UI in the same second is no longer missed.
- **whisper.cpp on Linux installs**: the pinned release archive keeps its internal library symlinks (`libwhisper.so -> libwhisper.so.1`), which `whisper-cli` needs to start; the install used to refuse every symlink and never finished. Links pointing outside the folder, absolute links and writes through a link are still refused.
- **Insert library keeps concurrent edits**: a rescan or a vision description run no longer overwrites "don't suggest", a hand-written description or a just generated (and paid) picture saved while it was running.
- **Media import**: a folder whose name only starts with the library folder name (`library_backup` next to `library`) is no longer skipped.
- **Paid video download**: the file is downloaded to a temporary name and kept only when it is complete (Content-Length) and is a video container (MP4/MOV or WebM); an HTML error page with status 200 or a cut-off body no longer becomes the result. Every provider link is tried, and a WebM result keeps the `.webm` extension.
- **Video status polling**: an HTML page, a JSON list or a read timeout counts as a failed poll instead of crashing; after four in a row the error names the task id and the status URL, so a paid result can still be collected.
- **Subtitle filter**: live speech containing "корректор" or "редактор субтитров" is kept when it was recognized confidently; credit lines with a name ("Корректор А. Егорова") are still removed.
- **Custom ASR engines file**: an `asr_engines.json` edited by hand into the wrong shape (`null`, a number, `engines` not a list, non-string fields) no longer breaks the engine list; the problem is logged.
- **GigaAM failures are visible**: a recognition error on a chunk stops the run with an error instead of turning that speech into an empty line; the temporary wav is unique and removed. Omni subtitles show the tail of the engine's error output.
- **Image generation no longer hangs**: when the OpenRouter Images API stays silent, the request falls back to chat completions after 40 s (90 s limit, then a clear `img_timeout` error); every image call is written to the AI call log.
- **AI highlights reach the word list**: a clip whose highlight set is empty reads it from the file, as the build does; re-running "highlights (AI)" resets and reloads the set.
- **Stop could delete an arbitrary folder**: a `WORK_DIR=` line printed inside a model's answer was trusted as the job's temp folder and removed on Stop; only the engine's own temp folder is accepted now.
- **Escaping in the interface**: file names and error texts are escaped before they go into HTML, and video history actions pass their keys through `data-*` attributes instead of inline JavaScript.
- **Saved API key and foreign address**: when the key field holds the mask, the connection check and model list use the saved profile's address and headers, not the ones from the request.
- **Malformed query parameters**: `pps` and `since` that are not numbers no longer return an HTTP 500 page.
- **Jobs stuck "busy"**: if the render, insert description, video generation or Google Drive download thread fails to start, its "running" flag and lock are released.
- **Two AI calls at once**: the wait for the previous AI call and taking the slot are one atomic step; insert description can no longer be started twice.
- **Orphan processes on exit**: stopping the web UI kills the running cut process and the After Effects render.
- **Tests in CI**: the breath detector revision test no longer needs `transformers`, and the environment check test passes in any interface language.
- **Media import into the insert library**: a successful import no longer reports "Failed to import media"; the answer carries the import log lines, the file count and the path of the import log file.
- **Wrong field types in API requests**: a number or list where a route expects a text field returns that route's usual error instead of an HTTP 500 "internal error" (camera loading and swap, `.drp` export, editor and XML state routes, insert library, style presets, AI inserts).
- **Unreadable file names in After Effects scripts**: lone surrogates (file names with undecodable bytes) and U+FFFE/U+FFFF are escaped in `.jsx` string literals, so writing the script no longer fails with `UnicodeEncodeError`.
- **Line separators in After Effects scripts**: U+2028 and U+2029 inside text are escaped, so a subtitle or intro word containing them no longer breaks the whole `.jsx` in ExtendScript; `verify_jsx` reports raw ones.
- **Control characters in XML**: characters that XML 1.0 forbids are removed from file names and text in Premiere XML, subtitle templates and `.drp`, so one such character no longer makes the whole file unreadable.
- **59.94 fps drop-frame timecode**: four frames per minute are dropped (not two), so a one-hour clip no longer drifts by 1.8 seconds in the `.drp` export.
- **SRT timing**: Premiere-route SRT uses the sequence frame rate instead of a fixed 60 fps, and times like 59.9996 s round to `00:01:00,000` instead of the invalid `00:00:60,000`.
- **Word highlight edits**: the Premiere XML is rewritten atomically, so a crash or Stop during the write no longer leaves an empty file; a failed backup is logged.
- **Render set validation**: `/api/render_run` checks the set before answering, so a broken set shows a clear error instead of "internal error".
- **Stuck downloads and renders**: a Google Drive download can be stopped with Stop and is killed after 10 minutes without output; `aerender` gets the same stall watchdog as After Effects.
- **Waveform cache**: `pps` is clamped to 10–1000, so an extreme value no longer writes a huge cache file.
- **Sound effect roles**: a malformed `assets/assets.json` or a path leaving the `assets` folder no longer breaks the script build; problems are logged.
- **Word editing in the interface**: a dead duplicate of the word save function was removed; which copy wins no longer depends on script file order.
- **Forced alignment**: word boundaries no longer shift by one letter after each word separator.
- **Tests**: subtitle width checks skip explicitly when the font is missing instead of passing silently.
- **Tests without torch**: the pinned-revision tests for RVM and CED-tiny and the "healthy environment" checks of `doctor.py` are skipped when torch is not installed, instead of failing; a healthy environment includes torch by definition, so these checks are not weakened.
- **Unhandled errors in API routes**: they return JSON with an error code instead of an HTML page, so the interface shows the error instead of staying silent.
- **Thread start failure**: if a cut or build thread cannot start, the job lock is released instead of leaving the app "busy" until restart.
- **Model answer with junk indices**: a malformed `drop` list from the model no longer crashes the Omni cut after the paid call.
- **Tests and docs**: a test that always passed now checks the SRT time format; the torch device test skips without torch; the docs freshness check sees `file.py:NNN` references and caught stale stage descriptions in `docs/CUTTING_SPEC.md`.
- **Provider errors in the API**: an unreachable or failing provider returns a JSON error with its hint instead of an HTTP 500 page, so the connection check in AI provider profiles no longer shows a syntax error.
- **Job status**: an exception passed into a log line no longer turns job, render and preview proxy status into an HTTP 500 page, so the interface keeps receiving progress.
- **Tests on Linux**: DaVinci Resolve export tests build media paths native to the OS, so CI on Ubuntu passes; the glitch glow API test no longer reads the local `ai_config.json`.
- **Request bodies that are not JSON objects**: an array, string or number sent to any API route returns a clear 400 error instead of an HTTP 500 page; a broken list field in a render or build set (for example `inserts: 5`) is reported by name.
- **Word highlights could be wiped by a malformed request**: `/api/set_yellow` with `indices` that is not a list now refuses and leaves the XML untouched.
- **Saving an AI profile with a masked key**: a saved address with a trailing slash no longer blocks saving when the address did not change; editing the address by hand clears the masked key just like switching the provider.
- **Google Drive stall watchdog on real rclone output**: progress is compared field by field, so a stalled transfer is stopped even while rclone keeps printing its statistics block, and hash checks after 100 % count as progress.
- **Inserts with a broken time but a quoted phrase**: the time is restored from the quote before non-numeric times are dropped, so a paid insert is no longer lost; an infinite number in the intro answer no longer breaks the step.
- **Read-only files and interrupted saves**: an atomic save refuses a read-only target like a plain write would, and a failure after the file was replaced no longer reports the save as failed.
- **Subtitle graphics at 25 and 29.97 fps**: Premiere tick values follow the sequence frame rate.
- **Install scripts**: `.venv` is ignored by git; `install.sh` explains a missing `python3-venv` and `REELSI_NO_VENV=1`; `install.ps1` also creates `.venv`.
- **Test isolation**: every job's state and the interface language and insert index caches are fully reset between tests; frontend fixes are checked by running the page code in node.
- **NTSC timelines were read as whole frame rates**: a 29.97 fps sequence (timebase 30 + `ntsc`) was treated as 30 fps, so an edit imported back from Premiere drifted by up to 3.6 seconds an hour; the real rate is used now, the built script carries it, and an empty `<timebase>` no longer breaks the build. DaVinci Resolve export refuses a timeline that is not 60 fps instead of writing frames in the wrong rate.
- **A saved API key could reach a new provider's address**: the key field shows a mask meaning "unchanged", and switching the provider replaced the address while keeping the mask — saving then paired the previous provider's real key with the new address. Such a save is refused now and the settings form clears the masked key when the provider changes.
- **Timeline download served any file**: `/api/export_xml` accepted any path (on Linux even `/proc/self/environ`) and sent it as an attachment when parsing failed; it now serves `.xml` only.
- **Wrong field types returned HTTP 500 across the API**: every request-body text field goes through one reader, so a number instead of a string gives the route's own error; a broken render or build set says which field is wrong instead of "file not found".
- **Stopping a Google Drive download looked like a failure**: Stop now shows "download stopped" instead of a red "download failed" toast.
- **Google Drive stall watchdog measured silence, not progress**: rclone prints statistics on a timer, so a stalled transfer looked alive; progress is now measured by the transferred bytes, and a failure inside the output reader no longer freezes the watchdog or kills a healthy download.
- **Stalled `aerender` on a multi-clip set**: the set path now has the same stall watchdog as a single clip — a silent render is reported and stopped instead of holding the job forever.
- **`/api/media`**: the extension and secret checks run before the file check, so the endpoint no longer reveals whether an arbitrary file exists; `.mpg`/`.mpeg` are served like the other video formats.
- **Camera swap with a non-numeric camera number** now says so instead of "camera 1 cannot be changed".
- **Durable saves on Linux and macOS**: after an atomic replace the folder is flushed too, so a saved file survives a power cut as the new version rather than silently reverting to the old one.
- **Atomic writes kept file permissions and symlinks**: saving a file through the atomic path reset its permissions to owner-only and replaced a symlink with a regular file; the target's permissions, owner and the link itself are kept now.
- **More of the user's files are written atomically**: style presets, speaker profiles, the learned filler-phrase list, the Premiere XML produced by the cut, the built `.jsx`, the subtitle XML, `.srt` and the `.drp` export — a crash or Stop mid-write no longer leaves an empty file. The architecture docs now say exactly what is atomic and what is a regenerable cache.
- **File names with undecodable bytes**: building the Premiere XML no longer fails with `UnicodeEncodeError`, and such a path survives the round trip through the XML.
- **`NaN` in a model answer**: an insert with a non-numeric or infinite time is dropped with a log line instead of crashing the `.jsx` build or producing JSON the browser cannot parse.
- **Claude answers cut by the output limit**: the Anthropic path reports the same error as the OpenAI one and checks the required fields, instead of logging a note and returning a partial answer.
- **Resolve title text**: a backslash or a line break in the title no longer breaks the Fusion composition of the `.drp` export.
- **Forced alignment with a non-zero pad token**: word boundaries no longer include blank spans.
- **Sound effect roles**: a role pointing outside the `assets` folder is logged instead of silently disappearing.
- **Sound effect trim waveform**: for effects shorter than 1.5 s the waveform uses the peak density the server actually returned, so its right part is no longer flat and trim points match the sound.
- **Install script**: `install.sh` creates `.venv` when run outside a virtual environment (system Python refuses `pip install` under PEP 668; `REELSI_NO_VENV=1` skips it) and prints the correct start command.
- **Tests**: the forced alignment regression tests run in CI (torchaudio is installed); subtitle checks that do not need the font run on machines without it; AI call and render tests no longer depend on file order (the per-thread call number and the "running" flag of every job are reset between tests); the test run writes its log to a temporary folder instead of the developer's `reelsi.log` and removes that folder at the end; the render lock test checks the dispatcher it actually exercises.
- **Route tests on Linux and Windows CI**: the camera swap test uses media paths native to the OS; the style overwrite test is skipped on a case-sensitive file system (detected by a probe, not by platform name); the insert library info test no longer depends on two writes landing in different timer ticks.
- **Command line**: `--help` of `core.omni_cut` and `core.gigaam_cut` no longer lists `--no-dedupe` twice and a `--no-no-dedupe` option.
- **NOTICE**: lists zstandard.
- **Language model calls**: a 400 error about `max_completion_tokens` or `stream_options` no longer loops the retry; the reasoning level on a retry is lowered from the previous attempt.
- **Local API**: state-changing requests from other sites are rejected by `Sec-Fetch-Site` and `Origin` checks.
- **Jobs**: render takes the shared job lock, so a cut can no longer start on top of a render.
- **Saved files**: JSON files and word lists are written atomically, so a stop or crash mid-save no longer leaves an empty file; concurrent `ai_config.json` saves no longer overwrite each other.
- **Premiere subtitle XML**: the subtitle track goes inside the sequence's video block, and the frame rate comes from the source XML.
- **Multicam**: until a late camera starts, its segment is taken from camera 1.
- **After Effects build**: an empty riser start time no longer breaks the script; a clip that fails in a batch build is reported as failed.
- **Editor and inserts**: the intro line colour input no longer throws a syntax error; insert sound preview follows the chosen file; the right insert is picked when the plan list is filtered; malformed entries in the model's insert answer are skipped; long words can be renamed in subtitle graphics; draft render accepts clip names with commas, brackets and quotes.
- **Build verification tools**: a one-`.jsx` build is checked against the selected timeline; empty middle intro groups are handled.

## 0.1.0-beta — 2026-09-12

### Added
- **Customizable cutting stages**: modular stage selection (pauses, transcription, semantic cut, duplicates, breath detection, draft render) and an interactive Custom modal in the web interface.
- **Batch rendering**: entire clip set rendered in a single After Effects project and single `aerender` launch, with dual clip and batch progress and ETA.
- **Style and markup controls**: collapsible style sections, subtitle scaling, photo overlays above subtitles, static inserts, and independent markup on selected clips.
- **Intro animations and speaker styles**: live preview for intro text animations, one-click speaker styling, and typography parity between the web player and After Effects.
- **Audio-based multicam synchronization**: automated alignment of multi-camera recordings by cross-correlating audio tracks.
- **Word-level transcription**: pluggable speech recognition backends with word-level alignment across faster-whisper, whisper.cpp, and GigaAM.
- **Semantic AI cutting**: silence cutting, breath filtering, duplicate take removal, and language-model-assisted cut selection.
- **Subtitle graphics generation**: styled subtitle graphics for Premiere Pro and After Effects with per-word highlights, smart line wrapping, and background box styling.
- **Visual inserts and asset generation**: semantic asset matching against a local library and on-demand AI image and video generation.
- **Intro text animation**: automated hook selection, multi-line animated typography, and speaker layout styling.
- **Rotoscoping**: GPU-accelerated background alpha mask generation with Robust Video Matting for depth layering behind the speaker.
- **Pre-render scene preview**: exact layout and coordinate computation rendered in an interactive web player with drag-and-drop adjustments before export.
- **Multi-format sequence export**: timeline export to Premiere Pro (XML), After Effects (.jsx scripts), and DaVinci Resolve (.drp projects).
- **Headless After Effects render**: automated batch rendering via `aerender` with progress tracking in the web interface.
- **Google Drive download**: remote footage ingestion via `rclone` with automated camera track distribution.
- **Environment diagnostics**: diagnostic tool checking dependency states, compute acceleration, and actionable fix instructions.

### Changed
- **Dynamic render phase weighting**: render progress weighted by measured phase times instead of fixed intervals.
- **Visual layer ordering**: draggable layer order in style settings replacing legacy boolean flags.

### Fixed
- **After Effects export consistency**: repeated script exports generate clean, deterministic scripts without stale state.
- **Windows breath detector stability**: resolved access violation on Windows by preloading pyarrow before PyTorch initialization.
