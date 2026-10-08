# Platforms: what to replace CUDA with on Mac and AMD

Russian version: [docs/PLATFORMS.md](PLATFORMS.md).

Not verified on live hardware: these are notes on porting to macOS, AMD and Linux. There is one verified platform — Windows + NVIDIA. The exception is section 8 (the built-in render without After Effects): it was taken from a live run on Linux (Ubuntu 24.04, RTX 3050 Laptop in a container).

Reelsi was written for Windows + NVIDIA + Adobe. The question "will it run on a Mac?" has no
single answer: the pipeline consists of a dozen pieces, and each has its own story. One
ports for free, another runs into the fact that the library it needs for that hardware
does not exist in nature.

Below is a breakdown piece by piece. Status as of August 2026.

## The short conclusion

| | Windows + NVIDIA | macOS (Apple Silicon) | Windows + AMD | Linux |
|---|---|---|---|---|
| Cutting, subtitles, XML | fully | yes, slower in places | yes, with caveats | yes |
| Rotoscope (RVM) | fully | noticeably slower | ~15-25% slower than CUDA | like AMD/NVIDIA |
| Draft render | NVENC | VideoToolbox, on par | AMF, slightly worse quality | VAAPI/NVENC |
| Premiere / After Effects | yes | **yes** | yes | **no Adobe at all** |

The main conclusion is not about the GPU: **on Linux it is not the computation that breaks, but the
pipeline's output** — Adobe does not exist there. On the other hand, the FCP7 XML that Reelsi
generates is imported by DaVinci Resolve, which does exist on Linux. That is, on Linux cutting with
subtitles into XML makes sense, but building graphics in After Effects does not.

On a Mac it is exactly the opposite: Adobe is there, and the computational part is what stumbles.

---

## 1. Transcription — the sorest spot

Now: **faster-whisper** on top of CTranslate2, CUDA + float16, and **whisper.cpp** as
an additional engine (Metal on Mac, Vulkan on AMD) — see "Done" below.

**CTranslate2 supports neither Metal nor ROCm.** This is not "slower" — it simply does not
exist, and on a Mac faster-whisper computes on the CPU. There is no way to speed it up on non-NVIDIA
hardware, only to change the engine.

Replacements:

- **whisper.cpp** — Metal and Core ML on Apple Silicon, on the order of 10× real time on
  large-v3. On AMD it builds with a Vulkan backend. It pulls in no Python dependencies at all,
  it is called as a separate binary — which by our architecture is a plus: `core/asr_backends.py` is already
  a registry of engines, and it is extended with one more.
- **MLX Whisper** (`lightning-whisper-mlx`) — Apple Silicon only, but natively for their
  unified memory.

The work: a new backend in `core/asr_backends.py` + `core/transcribe.py`. The architecture is already
ready for it — engines are registered through `data/asr_engines.json`.

**Done (2026-08-09, branch `feat/whisper-cpp`):** the engines `whisper.cpp:large-v3 /
medium / small` in `asr_backends.py`, the module `whisper_cpp.py` (the binary and ggml models
from HF, a separate process, `doctor.py`). Installation: `brew install whisper-cpp` /
`scoop install whisper-cpp` / apt, or the binary in `~/.reelsi/whisper_cpp/bin`
(the path is overridden by `REELSI_WHISPER_CLI`, the folder by `REELSI_WHISPER_CPP`).
Verified live on Windows (v1.9.2, CPU, jfk.wav → 22 words, timings correct).
Timecode units fluctuate between versions (v1.9+ — milliseconds, older ones — ticks) —
they are calibrated automatically. Word timings do not exist in the JSON of v1.9.2 at all (only
through DTW) — words are stretched onto segments by interpolation. What is left is to check
Metal itself on a live Mac.

## 2. GigaAM, Qwen2.5-Omni, forced-align, the breath detector

Now: plain PyTorch with `.cuda()`.

This ports most cheaply — PyTorch exists on all three platforms:

- **Apple Silicon — MPS.** `PYTORCH_ENABLE_MPS_FALLBACK=1` is needed: MPS does not cover every
  operation, and without this variable inference fails on the first unsupported one. Separately
  important: **MPS requires the model to fit entirely into unified memory** — `device_map="auto"`
  cannot offload layers to the CPU as on CUDA. For Omni this limitation is severe.
- **AMD — ROCm.** Since November 2025 PyTorch on Windows with ROCm officially exists (currently
  ROCm 7.2.1) for Radeon RX 7000/9000 and some Ryzen AI, but this is a public preview, not the whole
  ROCm stack. On Linux it is more mature. The lag behind CUDA is on the order of 15-25%.
- **DirectML** — works, but is **in maintenance mode**, there will be no new features; development
  moved to WinML. As a fallback for old Radeons it will do, as a bet — no.

Done: a single `device.py:pick_device` (`cuda → mps → cpu`) is wired into
`core/roto.py`/`core/breath.py`/`core/ctc_asr.py`/`core/falign.py`. What is left: `core/asr_backends.py` —
the default `device="cuda"` on the CTC engines (on Mac/AMD edit `data/asr_engines.json` by hand).

**Where the GigaAM weights live (important for a container and a service account).** The `gigaam` package
downloads them into the home folder by default — `~/.cache/gigaam` (421 MB, `v3_ctc.ckpt`;
the `emo` and `multilingual` heads — as separate files). Reelsi passes its own
`download_root`, and the folder is chosen in one place — `core/gigaam_cache.py`
(`gigaam_dir()`), in order: `REELSI_GIGAAM_CACHE` is set — it is taken (a person's explicit
choice beats everything else, including a ready cache); a ready
`~/.cache/gigaam` exists — it is taken (existing installations do not download a gigabyte
again); the home folder is writable — `~/.cache/gigaam` again; the home folder does not exist
or is read-only (a container, `HOME=/home/reelsi`, CI) — a spare folder
INSIDE the application folder, `_model_cache/gigaam` (it does not travel in git, `.gitignore`).
If that is not possible either — a `ReelsiError` with the reason, saying what exactly
is unavailable, and not somebody else's `PermissionError` from the bowels of the package: the code `gigaam_no_dir` for the
spare folder and `gigaam_env_no_dir` when the folder named by
`REELSI_GIGAAM_CACHE` is not writable (both have an English translation). The guard —
`tests/test_gigaam_dir.py` (it walks `core/` and `api/` by AST: every call to
`gigaam.load_model(` must pass `download_root`).

## 3. Rotoscope (RVM)

Now: Robust Video Matting through `torch.hub`, CUDA fp16, NVDEC decode.

The model is an ordinary convolutional one, it will run on MPS and ROCm. But this is the heaviest part of the pipeline,
and it will also lose the most performance: on a Mac fp16 on MPS works, but there is no
desktop NVIDIA speed there. Realistically — workable, but not "in the background while you drink coffee".

Separately: `_INTERNAL_PX` and `SEQ_CHUNK` were tuned for NVIDIA VRAM. On the unified memory
of a Mac, sharing memory with the system will have to be done differently — the constants must be recalibrated, otherwise
instead of an honest error you get swapping.

## 4. Draft render and camera proxies

Now: `h264_nvenc`, `-hwaccel cuda`, `scale_cuda`, a fallback to `libx264`.

Everything is fine here, because ffmpeg abstracts the hardware:

- **macOS — VideoToolbox** (`h264_videotoolbox`, `hevc_videotoolbox`), Apple Silicon's separate
  media engine. In quality it is competitive with NVENC.
- **AMD Windows — AMF** (`h264_amf`). The quality is slightly below NVENC, but acceptable.
- **AMD/Intel Linux — VAAPI**.

Done: `_CANDIDATES` per platform in `core/draftrender.py` (Darwin=videotoolbox,
Windows/Linux=nvenc/amf/qsv) on top of the "try a codec, on failure the next one" mechanics
(`tries`, `_nvenc_probe`); on non-NVIDIA `scale_cuda` is replaced by
the ordinary `scale` — the `vf_cpu` fallback is written. What is left: VAAPI (Intel/AMD Linux) —
it requires `-vaapi_device`, and it is not in `_CANDIDATES` yet.

## 5. rembg (removing the background from generated images)

Now: onnxruntime.

It ports through the execution provider, without touching the code:

- **macOS — CoreML EP** (CPU + GPU + Neural Engine).
- **Windows AMD — DirectML EP**.

The installation catch: `onnxruntime`, `onnxruntime-gpu`, `onnxruntime-directml` are one and
the same imported module. **They must not be mixed in one venv.** This has to be written explicitly
in the installation instructions, otherwise a person installs the second package on top of the first and gets
inexplicable crashes. (Not yet written in install.ps1/install.sh — TODO.)

## 6. Camera sync, VAD, XML assembly

scipy, numpy, silero-vad, the generation of FCP7 XML and `.jsx` — pure CPU. It works everywhere
the same, nothing to touch.

## 7. The pipeline's output

- **macOS** — Premiere and After Effects are there, ExtendScript works. The only
  caveat is that the SF Pro font is already installed system-wide on a Mac, that is, subtitles there will actually
  be built more correctly than on a clean Windows. Headless rendering is available only on
  Windows: the search for After Effects in doctor goes through `%ProgramFiles%\Adobe` (see
  `core/aerender.py:find_ae`); on a Mac the render is assembled by hand from the interface.
- **Linux** — there is no Adobe and there will not be. Generating `.jsx` is pointless. But FCP7 XML
  opens in **DaVinci Resolve**, which exists on Linux — so cutting with
  SRT subtitles remains useful. It is worth checking on a real XML: our markup
  uses Premiere specifics, and Resolve may not understand part of it.

---

## 8. Built-in render without After Effects: Chrome and the ANGLE backend

The second render engine ("Assemble and render" tab → built-in) does not call Adobe
at all: the frames are drawn by our own preview in headless Chrome (`core/webrender/capture.mjs`,
the DevTools protocol), and ffmpeg encodes with the master codec (`core/encoders.py`). Because of this it
has its own platform tie-in — not in the computation, but in WHAT the browser draws with.

**The ANGLE backend is chosen by platform.** `angle_backend` in `core/webrender.py` returns
`d3d11` on Windows, `vulkan` on Linux and `metal` on macOS (`ANGLE_BY_OS`, the same source of
the platform as the codec order) and travels to the capture script as the `--angle` key:
it is Python that knows the platform, not JS. The override is the `REELSI_ANGLE` variable (set and
not empty — it is taken), a door for a foreign driver: `gl`, `vulkan` or
`swiftshader` for checking.

**Why this is not cosmetics.** There used to be a hardcoded `--use-angle=d3d11` here — a request for
Direct3D 11, which does not exist outside Windows. Chrome did not "ignore the flag with
a fallback", it went into software SwiftShader: a measurement at 1080x1920 — 400-550 ms per
frame with the card idle (0% load, 9 W), whereas on the owner's machine 225 ms was measured. ANGLE's
backends are not portable: `d3d11` is Windows only.

**On Linux a Vulkan file descriptor (ICD) is needed; one flag is not enough.** In the container the
driver library was mounted (`libGLX_nvidia.so.0`), but `/usr/share/vulkan/icd.d/` was
empty — Vulkan has zero devices, and `--use-angle=vulkan` on its own does not help. The recipe:

```bash
mkdir -p /usr/share/vulkan/icd.d
printf '%s' '{"ICD":{"library_path":"libGLX_nvidia.so.0"}}' \
  > /usr/share/vulkan/icd.d/nvidia_icd.json
```

You have to check not with flags, but by asking the browser itself — with a page that prints
the WebGL renderer string (`--dump-dom`). Measurements on Ubuntu 24.04 + RTX 3050 Laptop:

| Flags | RENDERER |
|---|---|
| `d3d11` (was hardcoded) | `ANGLE (… SwiftShader Device (Subzero)), SwiftShader driver)` — processor |
| `--use-gl=egl`, `--use-angle=gl` | the same, SwiftShader |
| `--use-angle=vulkan` + ICD | `ANGLE (NVIDIA, Vulkan 1.4.329 (RTX 3050 Laptop GPU), NVIDIA)` — the card |

In the render log this is visible as the `#gpu` line (the capture script prints it): "hardware drawing —
…" or "software drawing (the card is unavailable) — …". The software path can be brought back with the
`REELSI_CAPTURE_NO_GPU=1` variable — a door for when hardware frames lie on somebody's machine.

**Chrome cleans up after itself.** When a piece finishes (and on the "Stop" button — the path is the same)
the capture script shuts down ITS OWN Chrome by PID together with the tree: on Windows `taskkill /F /T`, on
POSIX — its own process group (`detached`). Without the tree the browser itself and
its children kept living: on a Linux run after a render there were 155 Chrome processes hanging in the container, of
which 112 were zombies (`kill -9` does not take them — PID 1 must reap the orphans, see
`docker run --init`).

---

## What to do, in order

1. **A single `pick_device()`** (`cuda → mps → cpu`) instead of hardcoding in five files.
   Cheap, breaks nothing on Windows, and immediately brings a Mac to life for GigaAM/RVM/align.
2. **A codec list per platform** in `core/draftrender.py` on top of the existing fallback
   mechanics.
3. **whisper.cpp as an ASR backend** — the biggest task and the only way to give
   a Mac and AMD fast transcription. Done 2026-08-09 (see section 1); what is left is
   checking Metal on a live Mac.
4. **`doctor.py`** that honestly prints: what hardware was found, which backend will be
   used and what exactly is unavailable.
5. The onnxruntime providers and the warning about mixing packages — into the instructions.

## What is not here

Not a single line above has been verified on a live Mac or Radeon — we do not have that hardware.
This is an analysis of what is technically possible and at what cost, not a report on a working
port. Before promising support in the README, a real run is needed: at
minimum cutting one video and one rotoscope on each platform.

Sources (August 2026): [faster-whisper issue about Metal/MPS](https://github.com/SYSTRAN/faster-whisper/issues/515),
[a comparison of faster-whisper and whisper.cpp](https://codersera.com/blog/faster-whisper-vs-whisper-cpp-speech-to-text-2026/),
[AMD: PyTorch on Radeon under Windows](https://www.amd.com/en/blogs/2025/the-road-to-rocm-on-radeon-for-windows-and-linux.html),
[ROCm on Radeon — the compatibility matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/windows/windows_compatibility.html),
[HF: PyTorch on Apple Silicon (MPS)](https://huggingface.co/docs/transformers/en/perf_train_special),
[DirectML in maintenance mode](https://github.com/microsoft/DirectML),
[ffmpeg: hardware acceleration](https://deepwiki.com/FFmpeg/FFmpeg/7-hardware-acceleration),
[ONNX Runtime CoreML EP](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html).
