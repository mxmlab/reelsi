# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added
- **Feature guide**: `docs/FEATURES.md` and `docs/FEATURES.ru.md` describe every feature step by step: where it is, how to use it, settings, limits and cost.
- **Glitch glow choice**: yellow intro words with the glitch animation glow with the built-in Gaussian Blur and Glow or with the third-party Deep Glow 2 plugin (⚙ › Tools › After Effects).
- **Build progress for a multi-clip set**: the AE project build stage shows "N of M", the clip being built and the ETA; built clips are marked "built, waiting for render".
- **Package metadata**: `pyproject.toml` declares the name, the version and `requires-python >= 3.10`.

### Changed
- **CLA**: removed the internal author note from `docs/CLA.md`; `docs/CLA.md` and `.github/CONTRIBUTING.md` link CLA Assistant, which checks pull requests.
- **Contributing**: `.github/CONTRIBUTING.md` explains the task codes («задание GZ») found in comments and specs.
- **Demo**: the README demo is an animated WebP, 3.8 MB instead of the 8.4 MB GIF.
- **CI**: tests that need Pillow, pyarrow and zstandard run instead of being skipped; the workflow token is read-only; Python 3.12 and 3.13 byte-compile the code and check `--help` of the command-line entry points.
- **Security policy**: `.github/SECURITY.md` states that the breath detector loads its model with `trust_remote_code=True` and how to run without it.

### Fixed
- **Provider errors in the API**: an unreachable or failing provider returns a JSON error with its hint instead of an HTTP 500 page, so the connection check in AI provider profiles no longer shows a syntax error.
- **Job status**: an exception passed into a log line no longer turns job, render and preview proxy status into an HTTP 500 page, so the interface keeps receiving progress.
- **Tests on Linux**: DaVinci Resolve export tests build media paths native to the OS, so CI on Ubuntu passes; the glitch glow API test no longer reads the local `ai_config.json`.
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
