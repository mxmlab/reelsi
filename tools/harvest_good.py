# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Rebuild the subtitle template + blob library from a CORRECTED reference file.

The reference must contain GraphicAndType subtitle clips styled the way you want
(auto-width box so long words stay on one line, centered). Point this at that file:

  python tools/harvest_good.py "Reelsi_out/123.xml"

You can pass one or more .xml files, or a folder (then it globs *good*.xml).
Writes refblobs.json (blob per text byte-length) + sub_template.xml (clip structure
with Position/Anchor forced to horizontal centre 0.5).
"""
import re, base64, sys, glob, os, json
from typing import Sequence
# Скрипт живёт в tools/, репозиторий — на уровень выше: без корня в sys.path
# не найдётся пакет core.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import paths   # noqa: E402
from core.subtitle_blobs import _read_text   # noqa: E402

BLOB_RE = re.compile(
    r'<name>([^<]*)</name>\s*<effectid>GraphicAndType</effectid>.*?'
    r'<name>Source Text</name>\s*<hash>[0-9a-f-]+</hash>\s*<value>([A-Za-z0-9+/=]+)</value>',
    re.S)
CLIP_RE = re.compile(r'<clipitem id="clipitem-\d+">.*?</clipitem>', re.S)


def _center_xy(template: str) -> str:
    """Force the Position and Anchor Point X to 0.5 (screen / box centre), keep Y."""
    def fix(m: re.Match[str]) -> str:
        pre, y, post = m.group(1), m.group(3), m.group(4)   # group(2) = старый X, он и заменяется
        return f"{pre}0.5:{y}{post}"
    for pname in ("Position", "Anchor Point"):
        template = re.sub(
            r'(<name>' + pname + r'</name>\s*<value>[^,]+,)([0-9.eE+-]+):([0-9.eE+-]+)(,[^<]*</value>)',
            fix, template, count=1)
    return template


def resolve_files(args: Sequence[str]) -> list[str]:
    files = []
    for a in args:
        if os.path.isdir(a):
            files += glob.glob(os.path.join(a, "*good*.xml"))
        elif os.path.isfile(a):
            files.append(a)
    return sorted(set(files))


def main(args: Sequence[str]) -> None:
    files = resolve_files(args)
    if not files:
        sys.exit("no reference .xml found in: " + ", ".join(args))
    print("harvesting from:", [os.path.basename(f) for f in files])

    # blob library: keep the SMALLEST blob per byte-length (auto-width style; the
    # big ~874B blobs are FIXED-width and wrap long words — avoid them)
    by_len: dict[int, bytes] = {}
    for f in files:
        for name, val in BLOB_RE.findall(open(f, encoding="utf-8").read()):
            b = base64.b64decode(val)
            w = _read_text(b)
            if w is None or w != name.strip():
                continue
            L = len(w.encode("utf-8"))
            if L not in by_len or len(b) < len(by_len[L]):
                by_len[L] = b
    if not by_len:
        sys.exit("no usable subtitle blobs found")
    json.dump({str(k): base64.b64encode(v).decode() for k, v in by_len.items()},
              open(paths.data("refblobs.json"), "w"))
    print(f"refblobs.json: lengths {sorted(by_len)} (max {max(by_len)}B = {max(by_len)//2} cyr)")

    # template clip comes ONLY from the FIRST file (your corrected reference). It
    # MUST carry the FULL <file> definition (<mediaSource>GraphicAndType</...>),
    # not a bare <file id=.../> reference — otherwise Premiere can't resolve the
    # graphic and the whole sequence fails to import. Blob capacity for long words
    # is covered by refblobs above, so the template word length doesn't matter.
    best = None
    for blk in CLIP_RE.findall(open(files[0], encoding="utf-8").read()):
        if "GraphicAndType" not in blk or "<mediaSource>" not in blk:
            continue                                  # skip ref-only clips
        m = re.search(r'<name>([^<]*)</name>\s*<effectid>GraphicAndType', blk)
        w = m.group(1) if m else ""
        if best is None or len(w.encode()) > best[0]:
            best = (len(w.encode()), blk)
    if best is None:
        sys.exit(f"В {os.path.basename(files[0])} нет клипа-субтитра с полным "
                 "определением файла (<mediaSource>).")
    tmpl = _center_xy(best[1])
    print("template from:", os.path.basename(files[0]), "(full file def OK)")
    open(paths.data("sub_template.xml"), "w", encoding="utf-8").write(tmpl)
    print("sub_template.xml updated (Position/Anchor centred to 0.5)")


if __name__ == "__main__":
    from core.app_meta import out_dir
    args = sys.argv[1:] or [out_dir(os.path.dirname(paths.ROOT))]
    main(args)
