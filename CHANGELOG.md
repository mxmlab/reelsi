# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
