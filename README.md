# Reelsi

[![CI](https://github.com/mxmlab/reelsi/actions/workflows/ci.yml/badge.svg)](https://github.com/mxmlab/reelsi/actions/workflows/ci.yml)

Reelsi is a local editing assistant for talking-head video. AI cuts footage by content and builds ready-to-edit projects for Adobe Premiere Pro and After Effects — or renders the finished clip itself, without After Effects. Nothing is uploaded to remote servers, except what is needed by your chosen cloud AI provider: transcript text, and if cloud features are enabled, audio for transcription and images for insert description and generation; with local models, nothing leaves your machine.

> Russian version: [README.ru.md](README.ru.md).

![Reelsi in action](docs/img/demo.webp)

## What it does

### Cut

- **Multicam audio sync**: aligns several camera tracks automatically by audio correlation. Built for up to four cameras, verified on two.
- **Semantic AI cutting**: cuts speech by transcript content through a local model (LM Studio, Ollama) or a cloud provider. Cutting is a set of stages you choose: pauses, semantic cut, code cleanup of repeats and slips, snap to silence, breaths, draft mp4.
- **Word-level transcription**: pluggable speech recognition engines (Whisper, whisper.cpp, GigaAM) that give every word its own timing.
- **Second text pass (optional)**: the setting **Cutting engine (text)** (⚙ → Cut, off by default) lets Whisper correct the spelling of the words GigaAM recognised; the timings stay GigaAM's. With it on, the subtitles go into the XML already when the clip is cut.
- **Breath removal**: finds breaths before phrases and cuts them out; the disputed ones are shown as an orange strip in the editor and go with one click.
- **Cut editor**: corrects the result by hand — the cursor, the block edges, the removed pieces brought back with a double click, the cut-out audio for listening, and saving back to the XML. With the second text pass on, a piece brought back gets its subtitles again from the source words.
- **Batch queue**: many clips in one queue, cameras paired by name or by sound, and clips that already have a cut skipped.
- **Draft mp4**: a quick draft of every cut, to preview the result before markup.

### Preview and editing

- **In-browser scene preview**: the whole assembled scene — cuts, subtitles, inserts, intro — before export, with drag-and-drop edits of insert position, intro position and timing.
- **Camera layout**: chooses which camera is on screen for every piece, saves the layout and resets it to auto; a player lets you listen to another camera to check the sync.
- **Batch markup**: subtitles run one clip after another on the GPU, and the cloud steps (highlights, inserts) start for a clip as soon as its own subtitles are ready, so a whole set is marked up in one pass.
- **Insert editor**: timing, position and scale of every insert; a card keeps its identity even when several inserts share one file, and every edit can be undone and redone.
- **Clip work kept on disk**: speaker, camera setup, inserts, style and intro of a clip come back after a reload or a rescan, and a deleted clip goes to a recycle bin from which it is restored.
- **Roto and head tracking in the preview**: one button computes the speaker's cut-out figure and the head track on the GPU, with progress and Stop, and shows both in the preview.

### Sound

- **Voice processing per speaker**: an AI denoiser and a chain of VST3 plug-ins ("denoiser first, then plug-ins"), with live monitoring through the plug-in window and volume sliders; the processed voice is baked into one track shared by the preview and the project, and the cut is made by this cleaned voice.
- **Music**: kept in the style with a per-clip override; the chosen random track is shown by name, "Another track" re-picks it, and a link can be downloaded up front instead of during the build.

### Design

- **Word-level subtitles**: animated subtitle graphics with per-word highlights, smart line wrapping and a background plate, plus plain `.srt`.
- **Word highlights**: the AI picks the key words; yellow words rise, fade and blur in as they are spoken, stack one word at a time in a row, and a camera zoom lands only on the strongest ones.
- **Intro**: a hook and animated title cards built from the transcript, with accent rows, glow, shadow, per-camera position, scale and edge margins. The length of a title row is a style setting (20 characters by default), and a call word in quotes at the end of the video becomes the last accent.
- **Camera work**: zoom animation (smooth, jumps, drift), zoom-ins inside long takes and on highlighted words, framing by hand (fill, zoom point, frame offset, horizon) and head follow — with two cameras that each have their own zoom, framing, colour and intro.
- **Inserts**: photo and video assets matched from a local library by meaning or generated with an AI provider, or taken from the stock libraries Pexels, Unsplash, Pixabay and Openverse in that order (Coverr behind a flag); a plate mode cuts the photo's background out, and censoring hides a region with a mosaic. A word from your personal `named_inserts.json` dictionary (repository root; the shared example is `data/named_inserts.example.json`) puts its library picture on that word.
- **Background cut-out model**: u2net (fast, the default, about 0.7 s per picture) or BiRefNet (cleaner edges, about 8 s per picture on a processor), chosen in ⚙ → Generation; BiRefNet downloads its ~1 GB model on first use.
- **Roto**: GPU background matting puts the speaker's cut-out figure above the intro and the inserts.
- **Speaker and project styling**: a style panel with every knob, presets, a style bound to a speaker, and the layer order of the assembled scene.
- **Text extras**: a glossary of terms for recognition, censor word lists with a whole-word option, a caption under the video, and a disclaimer whose scale and position are adjusted in the preview.

### Export and render

- **Multiformat export**: timeline export to Premiere Pro (XML), After Effects (`.jsx` scripts) and DaVinci Resolve (`.drp` projects).
- **Headless After Effects render**: a set is built and rendered without opening the AE interface; several After Effects copies build the clips in parallel and one more merges them into a single project, with progress, ETA and Stop.
- **Built-in render without After Effects**: a second engine draws the frames with the very same scene preview in a windowless Chrome and mixes the audio from the scene plan, so After Effects is not needed at all. This path repeats the preview approximately — glow, Lumetri and the censor mosaic are close, motion blur is not there.
- **Progress, queue and logs**: every long job shows the clip, the queue and the stage, with a Stop button.

### Extras

- **Interface language**: Russian and English, switched in the header.
- **Command line**: cutting, highlights and the After Effects script are also available from the console, without the web interface.
- **Footage from Google Drive**: downloads source files from a shared link (`rclone`) and lays them out into camera folders.

## How processing works

1. **Sources**: one to four camera files in folders next to the repository; multi-camera tracks are aligned with each other by sound.
2. **Recognition**: the speech is transcribed with word-level timings (Whisper, whisper.cpp or GigaAM). With the optional second text pass, Whisper also corrects the spelling; the source words are kept next to the XML (`<stem>.srcwords.json`), so the subtitles of a piece brought back in the editor can be rebuilt.
3. **Cutting**: the transcript goes to a language model that decides what to remove, locally or through the chosen provider; the code then restores pauses, repeats and breaths and assembles the timeline.
4. **Editing**: the cut and the camera layout are corrected by hand in the editor.
5. **Markup and design**: subtitles, highlights, inserts, the intro and the camera work are computed per clip; assets come from the local library or from the AI provider.
6. **Assembly and render**: the scene plan becomes a Premiere XML, an AE script, a Resolve project, or a finished clip from the built-in engine.

Models — recognition, breaths, emotions, denoising — are computed by one local model service: it keeps the weights in memory instead of loading them per clip and counts several clips at once, up to a limit. Where it computes is a machine setting: the video card by default, the processor on a machine without a card or to keep the card free for a local LLM (LM Studio, Ollama). A render asks the service to release video memory before it starts.

## Requirements

Tested only on Windows 11 with an NVIDIA GPU and Adobe After Effects / Premiere Pro. Other platforms are untested; porting notes: [docs/PLATFORMS.md](docs/PLATFORMS.md).

- Python 3.10 and ffmpeg in PATH.
- PyTorch, installed separately and first, in the build for your hardware: a CPU build works too, but the GPU-bound steps then run tens of times slower (`install.ps1 -Cpu`).
- A Chromium-based browser (Chrome, Edge, Brave and the like). It is recommended for the preview: in Firefox the source audio of camera files with PCM tracks is not decoded at all, so the preview stays silent until it switches to the preview proxy. The built-in render needs a Chromium browser outright — it draws the frames in a windowless browser of its own.
- An NVIDIA GPU with CUDA support is what the project is verified on; models can also be computed on the processor (⚙ → "Cut" → "Where models are computed"). On a card more recognitions run in parallel and it is faster, on the processor no video memory is taken at all.
- A language model for the semantic steps — LM Studio or Ollama locally, or an API key for a cloud provider. The classic pause cutting with Whisper works without one.
- Adobe After Effects is optional: it is needed for the headless AE render and for editing the built `.jsx` by hand, while the built-in engine renders without it. Adobe Premiere Pro is optional as well — it is only needed to open the exported XML, Reelsi itself does not call it.
- Optional tools: `rclone` (Google Drive downloads), `yt-dlp` (music).
- Optional Python packages from `requirements-optional.txt` come one by one: every one enables exactly one feature and everything else keeps working (`pedalboard` — live monitoring through VST3 plug-ins and the output device list; `gigaam` — word-level cuts; `silero-vad`, `rembg`, `anthropic`, `yt-dlp`). `python doctor.py` names what is missing and what turns off without it.
- The AI denoiser arrives on demand: the RoFormer engine is a separate environment with its own models, downloaded from the Voice panel, and without it the previous deep-filter binary is used.
- Subtitle templates use SF Pro; rebuild them for another font via `python tools/harvest_good.py "path/to/reference.xml"`.

## Installation

Run all commands from the `reelsi/` clone folder.

Non-editable installation (`pip install .` or `pipx`) is not supported because templates, static assets, and user configuration files live inside the repository clone folder.

Run the automated installer — `install.ps1` on Windows, `install.sh` on macOS and Linux:

```bash
powershell -ExecutionPolicy Bypass -File install.ps1   # Windows
bash install.sh                                        # macOS and Linux
```

Both installers only install dependencies: the `reelsi`, `reelsi-webui` and `reelsi-doctor` commands appear after `pip install -e .`. Neither installs Python or ffmpeg — `install.sh` needs a system `python3` and both stop and tell you what is missing instead of installing it.

Or install manually:

```bash
pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu126
pip install -r requirements.txt
pip install -r requirements-optional.txt
```

Editable installation from clone (registers CLI commands `reelsi`, `reelsi-webui`, `reelsi-doctor`):

```bash
pip install -e .
# with optional dependencies:
pip install -e ".[optional]"
```

Dependency versions in `requirements.txt` carry an upper bound. Leave it in place: a fresh major release can break the subtitle step on a clean install — librosa 1.0 dropped its `audioread` fallback, and PyAV 19 removed an argument that faster-whisper 1.2 still passes, so PyAV is pinned below 19. If `pip check` or the subtitle step reports a version conflict, reinstall with the pinned versions rather than unpinning.

Verify your environment with `python doctor.py` (or `reelsi-doctor`), which checks installed tools, GPU acceleration, and missing components.

## Quick start

Clone the repository into your workspace, next to your footage folders:

```
my_workspace/
├── reelsi/            ← this repository
├── camera1/           ← camera footage
├── camera2/
└── music/
```

Start the web interface:

```bash
python webui.py          # or: reelsi-webui
```

Open http://127.0.0.1:5001 to run the three-step wizard (Cut, Markup, After Effects). On first run, `webui.py` creates `insertlib.json`, `styles/`, and `speakers/` from `examples/*.example.json`. Configure AI keys via ⚙ in the UI (stored in gitignored `ai_config.json`).

CLI commands:

```bash
python reelsi.py --cams 2     # two cameras (after pip install -e .: reelsi --cams 2)
python reelsi.py --single     # single camera
python reelsi.py --no-cut     # subtitles only
```

## Known limitations

- Verified only on Windows 11 with NVIDIA GPU and Adobe applications.
- The After Effects path is the only one verified end to end; the Premiere XML and `.drp` exports work but are tested less.
- Premiere XML and `.drp` export contain known issues documented in [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).
- The built-in render repeats the preview approximately: glow, Lumetri and the censor mosaic are close to the After Effects result, but motion blur is not there.
- The Lumetri colour in the browser preview is an approximation: the real Lumetri curves are closed, so the preview shows the direction of the correction, not the exact result.
- Beta status: configuration and project schemas may change between releases.

## Documentation

- [docs/FEATURES.md](docs/FEATURES.md) — every feature, step by step (English); Russian version: [docs/FEATURES.ru.md](docs/FEATURES.ru.md).
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — detailed architecture (Russian); English overview: [docs/ARCHITECTURE.en.md](docs/ARCHITECTURE.en.md).
- [docs/CUTTING_SPEC.md](docs/CUTTING_SPEC.md) — the full cutting architecture (Russian).
- [docs/INSERTS_SPEC.md](docs/INSERTS_SPEC.md) — photo and video inserts (Russian).
- [docs/INTRO_SPEC.md](docs/INTRO_SPEC.md) — the intro text behind the speaker (Russian).
- [docs/HIGHLIGHT_SPEC.md](docs/HIGHLIGHT_SPEC.md) — subtitle word highlights (Russian).
- [docs/DESIGN.md](docs/DESIGN.md) — interface style reference (Russian).
- [docs/DRP_SPEC.md](docs/DRP_SPEC.md) — DaVinci Resolve `.drp` export (Russian).
- [docs/PLATFORMS.md](docs/PLATFORMS.md) — porting notes for macOS, AMD and Linux (Russian).
- [docs/ROADMAP.md](docs/ROADMAP.md) — open directions and the history of decisions (Russian).
- [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) — known defects (Russian).
- [docs/CLA.md](docs/CLA.md) — contributor license agreement.
- [docs/TRADEMARK.md](docs/TRADEMARK.md) — trademark policy.
- [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md) — contribution guidelines.
- [CHANGELOG.md](CHANGELOG.md) — release history.

## Development

Install development dependencies and configure git hooks:

```bash
pip install -r requirements-dev.txt
pre-commit install
```

Run test suite and linters locally (CI validates the same checks):

```bash
python -m pytest tests -q
python -m pytest tests -q -n auto --dist loadgroup -m "not perf"   # as in CI
ruff check .
mypy
```

The second form is what CI runs: `-n auto` (pytest-xdist) spreads the suite over
workers, `--dist loadgroup` keeps the tests that start a real Chrome
(`xdist_group("chrome")`) inside one worker — several browsers at once measure
geometry unreliably — and `-m "not perf"` leaves out the time-budget tests, which
measure the runner rather than the code (they run locally). Keep the two flags
together: without the group the Chrome tests scatter over workers.

## License

Reelsi is licensed under AGPL-3.0-or-later. You may modify and redistribute it under the same license, or contact the author for commercial licensing.

- Third-party inventory: [NOTICE](NOTICE)
- Trademark policy: [docs/TRADEMARK.md](docs/TRADEMARK.md)
- Security policy: [.github/SECURITY.md](.github/SECURITY.md)

Third-party components:
- Adobe, Premiere Pro, and After Effects are trademarks of Adobe Inc., not affiliated with this project.
- Robust Video Matting is licensed under GPL-3.0 and downloaded when rotoscoping is used.
- GigaAM and Qwen2.5-Omni model weights are governed by their respective licenses.
- SF Pro font is not distributed with this repository.
- `yt-dlp`: users are responsible for downloaded media content.
