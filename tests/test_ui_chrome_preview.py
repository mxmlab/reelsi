# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Открытие предпросмотра на настоящем Chrome: дублёры, прокси, стык (серия B3, порция 5).

Открывают предпросмотр НАСТОЯЩИЕ openPreview / ipvOpen / cpvOpen. Их ответы — маршруты
/api/aicut_preview (настоящий EDL из XML на синтетических роликах) и /api/preview_proxy
(форма api/previewproxy.py, файлы прокси — ролики меньшего размера). См. tests/_video_stand.py.

Играет настоящий плеер: проверки смотрят, какой элемент в эфире, какой файл он играет и
сколько раз он перематывался. Функции предпросмотра не подменяются.

Группа xdist_group("chrome"), ролики — ffmpeg (skipif без ffmpeg).
"""
import pytest

from _chrome_stand import chrome_stand, run_steps
import _video_stand as VS

pytestmark = [pytest.mark.xdist_group("chrome"), chrome_stand,
              pytest.mark.skipif(not VS.FFMPEG, reason="синтетические ролики строит ffmpeg (в PATH)")]

# Двойники живого элемента: у каждого игрока свой набор буферов по камерам (bufMake / camBufs).
TWIN_JS = """(P => P.bufs.map(b => ({slot: b.slot,
    distinct: !P.vids.includes(b.el),
    same_file: (b.el.src || '').split('/').pop() === (P.vids[b.slot].src || '').split('/').pop()})))"""


def _open(fx, opener, *, proxy_ready=True, building=False, modal="mbInserts"):
    return VS.api_steps(fx, proxy_ready=proxy_ready, building=building) + [
        {"op": "eval", "js": "(async()=>{openModal('%s');await %s('%s');return true;})()"
                             % (modal, opener, fx["xml"])},
        {"op": "wait", "ms": 1500},
    ]


# ============== 1. КАЖДЫЙ ПЛЕЕР ДЕРЖИТ ДВОЙНОЙ БУФЕР НА СВОИ КАМЕРЫ ==============
def test_chrome_every_preview_player_keeps_a_double_buffer_for_its_cameras(tmp_path):
    """Предпросмотр шага 1 (PV), вставок (IPV) и раскладки камер (CPV): у каждой камеры в эфире
    есть отдельный дублёр — свой элемент, тот же файл; дублёр не показан как камера.

    Замена test_every_preview_player_uses_the_double_buffer (поведение трёх плееров на
    настоящих открытиях; проводка pvStep/spareRollAt в тексте — в старом тесте).
    """
    fx = VS.build(tmp_path)
    steps = _open(fx, "openPreview", modal="mbPreview")
    steps += [{"op": "eval", "js": "(async()=>{await ipvOpen('%s');await cpvOpen('%s');return true;})()" % (fx["xml"], fx["xml"])},
              {"op": "wait", "ms": 1500},
              {"op": "eval", "name": "pv", "js": "(%s)(PV)" % TWIN_JS},
              {"op": "eval", "name": "ipv", "js": "(%s)(IPV)" % TWIN_JS},
              {"op": "eval", "name": "cpv", "js": "(%s)(CPV)" % TWIN_JS}]
    res = run_steps(tmp_path, steps)
    for name, slots in (("pv", [0]), ("ipv", [0, 1]), ("cpv", [0, 1])):
        got = sorted(b["slot"] for b in res[name])
        assert got == slots, (name, res)
        assert all(b["distinct"] and b["same_file"] for b in res[name]), (name, res)


# ============== 2. ПРЕДПРОСМОТР ИГРАЕТ ПРОКСИ КАМЕР, НЕ ИСХОДНИК ==============
def test_chrome_every_preview_player_plays_the_camera_proxy_not_the_source(tmp_path):
    """Все три предпросмотра показывают прокси камер: файл в эфире — *_proxy.mp4 и кадр 160 px
    (меньший ролик), а исходник 320 px не открывается.

    Замена test_every_preview_plays_camera_proxies (поведение открытий; параметры pvSrc/encodeURIComponent
    в тексте — в старом тесте).
    """
    fx = VS.build(tmp_path)
    steps = _open(fx, "openPreview", modal="mbPreview")
    steps += [{"op": "eval", "js": "(async()=>{await ipvOpen('%s');await cpvOpen('%s');return true;})()" % (fx["xml"], fx["xml"])},
              {"op": "wait", "ms": 1500},
              {"op": "eval", "name": "play", "js": (
                  "(()=>{const f=P=>P.vids.map(v=>({file:(v.src||'').split('/').pop(),w:v.videoWidth,rs:v.readyState}));"
                  "return {pv:f(PV),ipv:f(IPV),cpv:f(CPV)};})()")}]
    res = run_steps(tmp_path, steps)["play"]
    for name in ("pv", "ipv", "cpv"):
        for v in res[name]:
            assert v["file"].endswith("_proxy.mp4") and v["w"] == 160 and v["rs"] >= 2, (name, res)


# ============== 3. ЗВУКОВАЯ КАМЕРА ТОЖЕ БУФЕРИЗУЕТСЯ ==============
def test_chrome_audio_camera_keeps_its_double_buffer_when_it_changes(tmp_path):
    """Раскладка камер: звуковая камера — та, с которой идёт звук; при смене звуковой камеры её
    дублёр остаётся (камера непрерывная дорожка, склейки проходят подменой).

    Замена test_camera_layout_buffers_the_audio_camera_too (поведение на открытии CPV; проводка
    cpvSyncAudioCam и оффсета в тексте — в старом тесте).
    """
    fx = VS.build(tmp_path)
    steps = _open(fx, "cpvOpen", modal="mbPreview") + [
        {"op": "eval", "js": "cpvAudio(1);true"},
        {"op": "wait", "ms": 400},
        {"op": "eval", "name": "st", "js": (
            "(()=>{const b1=CPV.bufs.find(b=>b.slot===1);"
            "return {audioCi:CPV.audioCi,slots:CPV.bufs.map(b=>b.slot).sort(),"
            "distinct:!!b1&&!CPV.vids.includes(b1.el),audioLive:CPV.vids[1]&&!CPV.vids[1].muted};})()")}]
    res = run_steps(tmp_path, steps)["st"]
    assert res["audioCi"] == 1, res
    assert res["slots"] == [0, 1] and res["distinct"] is True, res
    assert res["audioLive"] is True, res


# ============== 4. НА СТЫКЕ ЭЛЕМЕНТ ПОДМЕНЯЕТСЯ, А НЕ ПЕРЕМАТЫВАЕТСЯ ==============
def test_chrome_seam_swaps_the_element_instead_of_seeking(tmp_path):
    """Вставки (IPV): при игре через стыки кусков живой элемент подменяется дублёром (swap),
    перемотка на стыке не нужна (seek=0).

    Замена части test_preview_swaps_video_instead_of_seeking_at_the_cut (поведение на настоящем
    воспроизведении; проводка spareSwap/pvStep в тексте — в старом тесте). Ролик-камеры синтетический.
    """
    fx = VS.build(tmp_path)
    steps = _open(fx, "ipvOpen", modal="mbInserts") + [
        {"op": "eval", "js": (
            "(()=>{window.LAST=IPV.vids[0];window.CHG=0;window.STAT0=Object.assign({},IPV.stats);"
            "window.IV=setInterval(()=>{if(IPV.vids[0]!==window.LAST){window.CHG++;window.LAST=IPV.vids[0];}},40);"
            "ipvPlay();return true;})()")},
        {"op": "wait", "ms": 6000},
        {"op": "eval", "js": "clearInterval(window.IV);ipvPause();true"},
        {"op": "eval", "name": "st", "js": "({swap:IPV.stats.swap-STAT0.swap,seek:IPV.stats.seek-STAT0.seek,changes:CHG})"}]
    res = run_steps(tmp_path, steps)["st"]
    assert res["swap"] >= 2, res
    assert res["seek"] == 0, res
    assert res["changes"] >= 2, res


# ============== 5. ПРОКСИ ПЕРЕЕЗЖАЕТ НА СТЫКЕ, А НЕ В ЖИВОЕ ВИДЕО ==============
def test_chrome_proxy_arrives_at_a_seam_and_never_into_the_live_video(tmp_path):
    """Прокси дособрался во время игры: живой элемент src не получает — переезд идёт дублёром на
    стыке с разрывом; после такого стыка в эфире уже прокси.

    Замена test_proxy_moves_on_the_seam_not_in_the_live_video (поведение: журнал присвоений src
    в живой элемент и смена файла в эфире; проводка spareHandover/bufSwap в тексте — в старом тесте).
    Смежные куски EDL подмены не требуют (sparePrime их пропускает), поэтому играем дольше.
    """
    fx = VS.build(tmp_path)
    steps = _open(fx, "ipvOpen", modal="mbInserts", proxy_ready=False) + [
        {"op": "eval", "js": "(()=>{window.LIVE=IPV.vids[0];window.FIRST=null;window.MARK=null;"
                             "window.IV=setInterval(()=>{if(window.MARK===null)return;"
                             "if(IPV.vids[0]!==window.LIVE&&window.FIRST===null)window.FIRST=performance.now();},20);"
                             "ipvPlay();true;})()"},
        {"op": "wait", "ms": 800},
        *VS.proxy_steps(fx, ready=True),
        {"op": "eval", "js": "(async()=>{window.MARK=performance.now();await pvProxyPoll();return true;})()"},
        {"op": "wait", "ms": 15000},
        {"op": "eval", "js": "clearInterval(window.IV);ipvPause();true"},
        {"op": "eval", "name": "st", "js": (
            "(()=>{const end=window.FIRST||performance.now();"
            "const toLive=(window.__srcLog||[]).filter(([el,v,t])=>el===window.LIVE&&t>window.MARK&&t<end).length;"
            "return {toLive:toLive,liveNow:(IPV.vids[0].src||'').split('/').pop(),"
            "swapped:window.FIRST!==null};})()")}]
    res = run_steps(tmp_path, steps)["st"]
    assert res["toLive"] == 0, ("прокси записан в живой элемент до стыка", res)
    assert res["swapped"] is True, res
    assert res["liveNow"].endswith("_proxy.mp4"), res


# ============== 6. ПЛЕЕР ПОМНИТ ПУТИ КАМЕР И СЛЕДИТ ЗА ПРОКСИ ==============
def test_chrome_players_remember_camera_paths_and_watch_the_proxy_build(tmp_path):
    """Вставки (IPV) помнят пути своих камер (для переезда на прокси) и, пока прокси собираются,
    следят за сборкой; когда прокси готов — карта прокси получает файл камеры.

    Замена test_players_remember_camera_paths_and_watch_proxies (поведение: пути и опрос статуса;
    проводка ipvOpen в тексте — в старом тесте).
    """
    fx = VS.build(tmp_path)
    steps = _open(fx, "ipvOpen", modal="mbInserts", proxy_ready=False, building=True) + [
        {"op": "eval", "name": "watch", "js": "({poll: PVPX.poll!==0, cams: IPV.cams.map(c=>c.path), stage: PVPX.watch.indexOf('ipvstage')>=0})"},
        *VS.proxy_steps(fx, ready=True),
        {"op": "eval", "js": "(async()=>{await pvProxyPoll();return true;})()"},
        {"op": "eval", "name": "map", "js": "PVPX.map"}]
    res = run_steps(tmp_path, steps)
    w = res["watch"]
    assert w["poll"] is True and w["stage"] is True, res
    assert w["cams"] == [c["path"] for c in fx["cams"]], res
    assert res["map"].get(fx["cams"][0]["path"]) == fx["p1"], res


# ============== 8. СТЫК БЛОКА В РЕДАКТОРЕ — ЧЕРЕЗ ДУБЛЁРА, БЕЗ ПЕРЕМОТКИ ЖИВОГО ==============
def test_chrome_editor_block_seam_swaps_the_element_without_seeking_it(tmp_path):
    """Редактор шага 1 играет блоки и перепрыгивает вырезанные разрывы: живой <video> меняется на
    дублёра (новый элемент в эфире), и сразу после смены на нём не делают перемотку.

    Замена test_editor_playback_uses_the_same_double_buffer (поведение на настоящем предпросмотре;
    проводка edTick/edFollow/edJump в тексте — в старом тесте). Вырезы — разрывы в ED.blocks.
    """
    fx = VS.build(tmp_path)
    steps = _open(fx, "openPreview", modal="mbPreview") + [
        {"op": "eval", "js": (
            "(()=>{ED.blocks=[{s0:0,s1:2.5},{s0:4,s1:7},{s0:9,s1:12},{s0:14,s1:20}];ED.dur=170;ED.cs=0;"
            "window.CH=0;window.POST=0;window.LAST=PV.vids[0];window.BASE=0;window.TSW=0;"
            "window.IV=setInterval(()=>{const el=PV.vids[0];const now=performance.now();"
            "if(el!==window.LAST){window.CH++;window.LAST=el;window.BASE=el.__seeks||0;window.TSW=now;}"
            "else if(now-window.TSW<300&&(el.__seeks||0)>window.BASE){window.POST++;window.BASE=el.__seeks||0;}},20);"
            "edPlay();return true;})()")},
        {"op": "wait", "ms": 9000},
        {"op": "eval", "js": "clearInterval(window.IV);edPause();true"},
        {"op": "eval", "name": "st", "js": "({changes:CH,postSeeks:POST})"}]
    res = run_steps(tmp_path, steps)["st"]
    assert res["changes"] >= 2, ("на разрывах редактора элемент в эфире не менялся", res)
    assert res["postSeeks"] == 0, ("сразу после смены элемента его перематывали", res)
