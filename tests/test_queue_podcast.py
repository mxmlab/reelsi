# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Рилсы подкаста в очереди шага 1 (П7, часть Б).

Рилс кладётся в очередь записью из ОДНОГО абсолютного пути `[abs]`. Что это значит для
сервера и клиента:
1. build_pairs не портит абсолютный путь (os.path.join отдаёт второй аргумент целиком);
2. смешанная очередь `[[c1, c2], [abs_reel]]` уходит в движок парами длины 2 и 1 —
   число камер берётся из длины пары, а не из папок;
3. признак абсолютного пути в клиенте (`isAbsPath` в 40-queue.js) узнаёт `C:\\x`, `\\\\srv\\x`, `/x`.

Запуск: py -3.10 -m pytest tests/test_queue_podcast.py -q
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api.jobs as jobs  # noqa: E402
from api._core import JOB, LOCK  # noqa: E402

QUEUE_JS = os.path.join(ROOT, "static", "app", "40-queue.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def _layout(tmp_path):
    """Две папки камер с клипами и рилс подкаста в отдельной папке."""
    cam1 = tmp_path / "cam1"
    cam2 = tmp_path / "cam2"
    pod = tmp_path / "pod" / "reels"
    for d in (cam1, cam2, pod):
        d.mkdir(parents=True)
    (cam1 / "a.mp4").write_bytes(b"x")
    (cam2 / "b.mp4").write_bytes(b"x")
    reel = pod / "x_01.mp4"
    reel.write_bytes(b"x")
    return str(cam1), str(cam2), str(reel)


def test_build_pairs_keeps_absolute_reel_path_as_is(tmp_path):
    cam1, cam2, reel = _layout(tmp_path)
    pairs = jobs.build_pairs([cam1, cam2], [["a.mp4", "b.mp4"], [reel]])
    assert pairs[0] == [os.path.join(cam1, "a.mp4"), os.path.join(cam2, "b.mp4")]
    assert pairs[1] == [reel]                       # одна запись — полный путь без склейки


def test_build_pairs_reel_survives_any_camdir(tmp_path):
    # Переключатель камер меняет папки; рилс с абсолютным путём от них не зависит.
    cam1, cam2, reel = _layout(tmp_path)
    other = str(tmp_path / "elsewhere")
    os.makedirs(other)
    assert jobs.build_pairs([other], [[reel]]) == [[reel]]
    assert jobs.build_pairs([cam1, cam2], [[reel]]) == [[reel]]


def _mixed_run(monkeypatch, tmp_path, pairs):
    """Прогон run_omnicut_job с подменённым движком: Popen пишет cmd и тут же выходит."""
    captured = []
    real_popen = jobs.subprocess.Popen

    def rec_popen(cmd, *a, **kw):
        captured.append(list(cmd))
        return real_popen([sys.executable, "-c", "pass"], *a, **kw)

    monkeypatch.setattr(jobs.subprocess, "Popen", rec_popen)
    monkeypatch.setattr(jobs, "module_cmd", lambda mod, *args: [sys.executable, "-c", "pass"])
    monkeypatch.setattr(jobs, "items_init", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_set", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_done", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_fail", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "job_finish", lambda: None)
    monkeypatch.setattr(jobs, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "set_stalled", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "emit", lambda *a, **k: None)
    from core import aicut
    monkeypatch.setattr(aicut, "cut_parallel_width", lambda m, r, n: 1)
    with LOCK:
        JOB.update(cancel=False, running=True, done=False, results=[], failed=[], items=[])

    jobs.run_omnicut_job(str(tmp_path / "out"), pairs, stages={"draft": False})
    return captured


def test_mixed_queue_two_camera_pair_and_one_camera_reel(tmp_path, monkeypatch):
    cam1, cam2, reel = _layout(tmp_path)
    pairs = jobs.build_pairs([cam1, cam2], [["a.mp4", "b.mp4"], [reel]])
    captured = _mixed_run(monkeypatch, tmp_path, pairs)

    assert len(captured) == 2
    cams = [[cmd[i + 1] for i, t in enumerate(cmd) if t == "--cam"] for cmd in captured]
    assert cams[0] == [os.path.join(cam1, "a.mp4"), os.path.join(cam2, "b.mp4")]
    assert cams[1] == [reel]                        # рилс — одна камера, не две


def test_camera_count_follows_pair_length_not_camdirs(tmp_path, monkeypatch):
    # Две папки камер в запросе, но пара одна: режется одной камерой.
    cam1, cam2, reel = _layout(tmp_path)
    pairs = jobs.build_pairs([cam1, cam2], [[reel]])
    captured = _mixed_run(monkeypatch, tmp_path, pairs)
    assert [cmd.count("--cam") for cmd in captured] == [1]


@node
def test_is_abs_path_in_queue_js(tmp_path):
    """isAbsPath из 40-queue.js: абсолютные (диск, UNC, корень) — да, имя файла — нет."""
    with open(QUEUE_JS, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"function isAbsPath\(p\)\{[^}]*\}", src)
    assert m, "в 40-queue.js нет функции isAbsPath(p) в одну строку"
    cases = {"C:\\x": True, "\\\\srv\\x": True, "/x": True, "a.mp4": False,
             "": False, "D:/pod/reels/x_01.mp4": True, "clip_01.mp4": False}
    script = m.group(0) + "\nconst cases = " + json.dumps(cases) + ";\n" \
        "const out = {}; for (const k of Object.keys(cases)) out[k] = isAbsPath(k);\n" \
        "process.stdout.write(JSON.stringify(out));\n"
    path = tmp_path / "is_abs_check.js"
    path.write_text(script, encoding="utf-8")
    res = subprocess.run(["node", str(path)], capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    for k, want in cases.items():
        assert got[k] is want, f"isAbsPath({k!r}) должен быть {want}"
