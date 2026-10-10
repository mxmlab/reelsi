# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стенд открытия предпросмотра: синтетические ролики (ffmpeg) и ответы маршрутов.

* XML — обезличенная фикстура tests/fixtures/timeline_nosubs.xml с путями, переписанными на
  синтетические ролики в tmp_path (цвет кадра: кам1 — красный, кам2 — синий).
* EDL (/api/aicut_preview) — ответ НАСТОЯЩЕГО маршрута (api/editor.py → core/xml2ae.virtual_edl).
* Прокси (/api/preview_proxy) — ответ той же формы, что у api/previewproxy.py; файлы прокси —
  синтетические ролики меньшего размера (кам1 — зелёный 160x120, кам2 — жёлтый 160x120).
  Сборщик прокси заглушкой не подменяется — он просто не запускается (build не нужен стенду).

Функции предпросмотра (openPreview, ipvOpen, cpvOpen, bufMake, spareSwap, pvProxyLoad, …)
не подменяются: проверяется их настоящая работа на этих роликах.
"""
import json
import os
import pathlib
import shutil
import subprocess
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "timeline_nosubs.xml")
FFMPEG = shutil.which("ffmpeg")
DURATION_S = 170          # как у фикстуры: EDL покрывает весь монтаж


def _make(path, color, size, seconds=DURATION_S):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=%s:s=%s:r=25:d=%d" % (color, size, seconds),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
                   check=True, timeout=300)
    return str(path).replace("\\", "/")


def build(tmp_path, proxies=True):
    """Ролики, XML на них, настоящий EDL и ответ прокси. Возвращает словарь для шагов стенда."""
    import webui
    d = pathlib.Path(tmp_path)
    cam1 = _make(d / "cam1.mp4", "red", "320x240")
    cam2 = _make(d / "cam2.mp4", "blue", "320x240")
    p1 = _make(d / "cam1_proxy.mp4", "green", "160x120") if proxies else None
    p2 = _make(d / "cam2_proxy.mp4", "yellow", "160x120") if proxies else None
    xml = open(FIXTURE, encoding="utf-8").read()
    xml = xml.replace("file://localhost/C%3a/footage/cam1/CLIP-030.MP4", "file:///" + cam1)
    xml = xml.replace("file://localhost/C%3a/footage/cam2/CLIP-031.MP4", "file:///" + cam2)
    xml_path = d / "timeline.xml"
    xml_path.write_text(xml, encoding="utf-8")
    xml_s = str(xml_path).replace("\\", "/")
    edl = webui.app.test_client().post("/api/aicut_preview", json={"xml": xml_s},
                                       headers={"Host": "127.0.0.1:5001"}).get_json()
    assert edl.get("ok"), edl
    cams = [{"name": c.get("name", ""), "path": c["path"]} for c in edl["cams"]]
    proxy_cams = [{"path": cams[0]["path"], "proxy": p1, "ready": bool(p1)},
                  {"path": cams[1]["path"], "proxy": p2, "ready": bool(p2)}] if proxies else []
    return {"xml": xml_s, "edl": edl, "cams": cams, "proxy_cams": proxy_cams,
            "cam1": cam1, "cam2": cam2, "p1": p1, "p2": p2}


def api_steps(fx, *, proxy_ready=True, building=False):
    """Заглушки /api: EDL — настоящий ответ; прокси — ответ формы api/previewproxy.py."""
    edl = json.dumps(fx["edl"], ensure_ascii=False)
    pcams = [{"path": c["path"], "proxy": c["proxy"], "ready": proxy_ready and c["ready"]}
             for c in fx["proxy_cams"]]
    prox = json.dumps({"ok": True, "dir": "", "building": building, "cams": pcams, "extra": []},
                      ensure_ascii=False)
    status = json.dumps({"ok": True, "running": bool(building), "done": not building, "i": 2, "n": 2, "pct": 100,
                         "cur": "", "log": []}, ensure_ascii=False)
    return [{"op": "eval", "js": (
        "window.__api['/api/aicut_preview']=()=>(" + edl + ");"
        "window.__api['/api/preview_proxy']=()=>(" + prox + ");"
        "window.__api['/api/preview_proxy_status']=()=>(" + status + ");true")}]


def media_name(url):
    """Имя файла из src элемента (file:///C:/…/cam1.mp4 -> cam1.mp4)."""
    return urllib.parse.unquote(url).replace("\\", "/").rsplit("/", 1)[-1]


def proxy_steps(fx, *, ready):
    """Подменить ответ /api/preview_proxy на лету (прокси дособрали, или ещё нет)."""
    pcams = [{"path": c["path"], "proxy": c["proxy"], "ready": ready and c["ready"]}
             for c in fx["proxy_cams"]]
    prox = json.dumps({"ok": True, "dir": "", "building": False, "cams": pcams, "extra": []},
                      ensure_ascii=False)
    status = json.dumps({"ok": True, "running": False, "done": True, "i": 2, "n": 2, "pct": 100, "cur": "", "log": []}, ensure_ascii=False)
    return [{"op": "eval", "js": "window.__api['/api/preview_proxy']=()=>(" + prox + ");" "window.__api['/api/preview_proxy_status']=()=>(" + status + ");true"}]
