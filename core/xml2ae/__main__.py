# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Командная строка пакета:  python -m core.xml2ae EditedSequence.xml [out.jsx]

Раньше это был хвост xml2ae.py и запускалось как `python xml2ae.py`. Блок
переехал дословно; сменился только способ запуска.

`--render-dir` (задание BD): папка вывода безголового рендера. Задана — .jsx сам
ставит очередь рендера, сохраняет .aep рядом с собой и закрывает AE
(AfterFX.exe -noui -r + aerender.exe -project). По умолчанию — папка `exp`
РЯДОМ с репозиторием: он публичный, чужим рендерам в нём не место.
"""
import os

from core import paths

from .build import to_ae_full

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="python -m core.xml2ae")
    ap.add_argument("xml"); ap.add_argument("jsx", nargs="?")
    ap.add_argument("--music", help="YouTube URL или путь к аудио — добавить музыкой в AE")
    ap.add_argument("--music-db", type=float, default=-20.0)
    ap.add_argument("--music-dir", help="куда качать музыку (по умолчанию папка music рядом с XML)")
    ap.add_argument("--render-dir", default=None,
                    help="папка вывода .mov для безголового рендера (задание BD); "
                         "по умолчанию папка exp рядом с репозиторием")
    a = ap.parse_args()
    if a.render_dir is None:
        # дефолт — exp рядом с репозиторием, а не внутри (репозиторий публичный)
        repo = paths.ROOT
        a.render_dir = os.path.join(os.path.dirname(repo), "exp")
    try:
        out, nclips, nsubs = to_ae_full(a.xml, a.jsx, music=a.music, music_db=a.music_db,
                                        music_dir=a.music_dir, render_dir=a.render_dir)
    except ValueError as e:                  # понятный текст вместо трейсбека в CLI
        raise SystemExit(str(e))
    print(f"-> {out}  ({nclips} видео-клипов, {nsubs} субтитров)")
