# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Resolve project assets (SFX / transitions) by logical role via assets/assets.json.
Non-destructive: keeps original filenames, the JSON maps role -> file. Missing role
or file -> empty string (feature just skips that asset)."""
import os, json

ROLES = ("intro_riser", "whoosh", "transition", "highlight_pop", "glitch")


def resolver(base):
    d = os.path.join(base, "assets")
    cfg = os.path.join(d, "assets.json")
    m = {}
    if os.path.isfile(cfg):
        try:
            m = json.load(open(cfg, encoding="utf-8"))
        except Exception:
            m = {}

    def path(role):
        fn = (m.get(role) or "").strip()
        if not fn:
            return ""
        p = os.path.join(d, fn)
        return p if os.path.isfile(p) else ""

    return path
