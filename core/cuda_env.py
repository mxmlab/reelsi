# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Register the pip-installed NVIDIA CUDA runtime DLLs so ctranslate2/faster-whisper
can use the GPU on Windows. Import and call setup() BEFORE importing faster_whisper.
See memory: faster-whisper-cuda-windows-dll-fix.
"""
import os, site


def setup() -> list[str]:
    bases = set(site.getsitepackages() + [site.getusersitepackages()])
    dirs = []
    for base in bases:
        for sub in ("cublas", "cudnn", "cuda_runtime"):
            d = os.path.join(base, "nvidia", sub, "bin")
            if os.path.isdir(d):
                dirs.append(d)
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
        # getattr, а не прямой вызов: os.add_dll_directory объявлен в typeshed только для
        # win32, а mypy под --platform linux даёт [attr-defined].
        add_dll_directory = getattr(os, "add_dll_directory", None)
        if add_dll_directory is not None:
            for d in dirs:
                try:
                    add_dll_directory(d)
                except OSError:
                    pass  # каталог не принят как DLL-путь — PATH уже дополнен выше
    return dirs
