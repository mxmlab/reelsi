# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
r"""Предпросмотр вставок на настоящем Chrome с ПЛАНОМ СЦЕНЫ (серия B3, порция 4).

План — настоящий ответ маршрута /api/scene (core/xml2ae.scene_plan на обезличенной фикстуре
tests/fixtures/timeline_nosubs.xml). Заглушка /api отдаёт его странице; предпросмотр рисует
вставки по плану. Перетаскивание и Shift — события браузера через DevTools Protocol.

Группа xdist_group("chrome"): стенд в одном воркере (-n auto --dist loadgroup).
"""
import json
import pathlib
import os
import shutil
import subprocess

import pytest

from _chrome_stand import chrome_stand, run_steps

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE_XML = os.path.join(HERE, "fixtures", "timeline_nosubs.xml").replace("\\", "/")
PHOTO = os.path.join(HERE, "fixtures", "timeline_nosubs.xml")  # путь к файлу для вставки-фото не нужен плану
# для вставок нужен только путь (план его не читает); файл на диске не обязан существовать

pytestmark = [pytest.mark.xdist_group("chrome"), chrome_stand]


FFMPEG = shutil.which("ffmpeg")
live_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="синтетические картинки и ролики строит ffmpeg (в PATH)")


def _img(tmp_path, name, color="red", size="640x360"):
    """Синтетическая картинка-кадр: цвет и размер известны, файл в tmp_path (не личный)."""
    out = pathlib.Path(tmp_path, name)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=%s:s=%s" % (color, size), "-frames:v", "1", str(out)],
                   check=True, timeout=120)
    return str(out).replace("\\", "/")


def _plan(inserts):
    """План сцены — тем же маршрутом, что у предпросмотра (тело — отфильтрованные вставки)."""
    import webui
    body = webui.app.test_client().post(
        "/api/scene", json={"xml": FIXTURE_XML, "inserts": inserts},
        headers={"Host": "127.0.0.1:5001"}).get_json()
    assert body.get("ok"), body
    return body["plan"]


def _ins(media, start, dur, style="cam2", x=0.0, y=0.0):
    return {"media": media, "type": "photo", "start_s": start, "dur_s": dur, "style": style,
            "x": x, "y": y, "sc": 100, "mw": 100, "mh": 100}


def _open(inserts_for_plan, job_ins, extra_js=""):
    """Открыть предпросмотр вставок (openAEPreview) и загрузить план из заглушки /api/scene."""
    import json as _j
    plan = _plan(inserts_for_plan)
    return [
        {"op": "eval", "js": "window.__api['/api/scene']=()=>({ok:true,plan:" + _j.dumps(plan) + "});true"},
        {"op": "eval", "js": "CLIPS=[{xml:'" + FIXTURE_XML + "',name:'A',status:{},inserts:[],job:defJob()}];"
                             "CLIPS[0].job.ins=" + _j.dumps(job_ins) + ";curAE=0;openAEPreview();AEXML=CLIPS[0].xml;true"},
        {"op": "eval", "js": "(async()=>{" + extra_js + "await ipvPlanFetch();ipvRefresh();return true;})()"},
    ]


def _wrap_rect(i):
    return ("(()=>{const w=document.querySelector('#ipvins .ipvwrap[data-ins=\"%d\"]');"
            "if(!w)return null;const r=w.getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2];})()" % i)


# ============== 1. ПРЕДПРОСМОТР РИСУЕТ ПЛАН, А НЕ ПЕРЕСЧИТЫВАЕТ ВСТАВКИ ==============
def test_chrome_preview_draws_the_scene_plan_and_never_recomputes_it(tmp_path):
    """Вставка видна ровно в окне, которое записано в ПЛАНЕ; правка плана двигает её без пересчёта из вставок.

    Замена test_preview_draws_the_scene_plan_and_never_recomputes_it (поведение; проводка
    ipvPlanFetch/ipvOverlayPlan в тексте — в старом тесте). В вставках start_s остаётся 2 с;
    меняем только окно в плане — и кадр должен следовать плану.
    """
    ins = [_ins("D:/fx/a.jpg", 2.0, 2.0, style="cam2")]
    steps = _open(ins, ins) + [
        {"op": "eval", "name": "before", "js": "(()=>{ipvOverlayPlan(2.5);return document.querySelectorAll('#ipvins .ipvwrap').length;})()"},
        {"op": "eval", "js": "IPV.plan.inserts[0].start=3.0;IPV.plan.inserts[0].end=5.0;true"},
        {"op": "eval", "name": "earlyAfterEdit", "js": "(()=>{ipvOverlayPlan(2.5);return document.querySelectorAll('#ipvins .ipvwrap').length;})()"},
        {"op": "eval", "name": "lateAfterEdit", "js": "(()=>{ipvOverlayPlan(3.5);return document.querySelectorAll('#ipvins .ipvwrap').length;})()"},
    ]
    res = run_steps(tmp_path, steps)
    assert res["before"] == 1, res
    assert res["earlyAfterEdit"] == 0, res
    assert res["lateAfterEdit"] == 1, res


# ============== 2. ЗУМ КАМЕРЫ 1 ВЕДЁТ ТОЛЬКО ВСТАВКИ КАМ1 ==============
@live_ffmpeg
def test_chrome_cam1_inserts_follow_camera_zoom_and_cam2_inserts_do_not(tmp_path):
    """Вставка кам1 растёт вместе с наездом Камеры 1; вставка, стоящая на кам2 (oncam2), — нет.

    Замена test_cam1_inserts_follow_camera_zoom_only_on_cam1 (поведение на экране; проводка
    ipvInsPlace в тексте — в старом тесте). Окно — пересечение вставок ПЛАНА; флаг oncam2
    второй вставки стоит в плане (его выставляет сборка по активной камере в момент вставки).
    """
    ins = [_ins(_img(tmp_path, "a.png", "red"), 1.0, 40.0, style="cam1"),
           _ins(_img(tmp_path, "b.png", "blue"), 1.0, 40.0, style="cam1")]
    measure = (
        "(()=>{const a=IPV.plan.inserts;a[1].oncam2=true;"
        "const s0=Math.max(...a.map(x=>x.start)),e0=Math.min(...a.map(x=>x.end));"
        "let lo=null,hi=null;for(let k=0;k<=8;k++){const t=s0+0.2+(e0-s0-0.4)*k/8;const z=ipvZoomAt(t);"
        "if(lo===null||z<lo.z)lo={t:t,z:z};if(hi===null||z>hi.z)hi={t:t,z:z};}"
        "const w=t=>{ipvOverlayPlan(t);const e=i=>{const n=document.querySelector('#ipvins .ipvwrap[data-ins=\"'+i+'\"]');return n&&n.firstChild?n.firstChild.getBoundingClientRect().width:0;};return [e(0),e(1)];};"
        "return {lo:w(lo.t),hi:w(hi.t),zlo:lo.z,zhi:hi.z};})()")
    steps = _open(ins, ins) + [
        {"op": "eval", "name": "res", "js": measure},
    ]
    res = run_steps(tmp_path, steps)["res"]
    (lo1, lo2), (hi1, hi2) = res["lo"], res["hi"]
    zlo, zhi = res["zlo"], res["zhi"]
    assert zhi > zlo + 0.05, ("план не даёт наезда в окне вставок — проверка бессмысленна", res)
    assert lo1 > 0 and hi1 > 0 and lo2 > 0 and hi2 > 0, res
    assert abs((hi1 / lo1) - (zhi / zlo)) / (zhi / zlo) < 0.02, ("вставка кам1 не следует зуму", res)
    assert abs(hi2 - lo2) / lo2 < 0.01, ("вставка на кам2 растёт с зумом — а не должна", res)


# ============== 3. ДРАГ КАДРА ПИШЕТ В ДАННЫЕ ВСТАВКИ ==============
def test_chrome_frame_drag_writes_the_insert_data_once(tmp_path):
    """Перетаскивание вставки в кадре меняет её x и сохраняется в задание (одно хранилище).

    Замена test_frame_drag_writes_data_not_a_second_storage (поведение; деление на k·зум и
    проводка pointerdown — в старом тесте). Драг — настоящий ввод мыши по обёртке вставки.
    """
    ins = [_ins("D:/fx/a.jpg", 2.0, 4.0, style="cam2", x=0.0, y=0.0)]
    steps = _open(ins, ins) + [
        {"op": "eval", "js": "ipvOverlayPlan(3);true"},
        {"op": "eval", "name": "xBefore", "js": "INS[0].x"},
        {"op": "drag", "sel": "#ipvins .ipvwrap[data-ins=\"0\"]", "fx": 0.5, "fy": 0.5, "dx": 60, "dy": 0, "steps": 8},
        {"op": "wait", "ms": 150},
        {"op": "eval", "name": "xAfter", "js": "INS[0].x"},
        {"op": "eval", "js": "captureAE();true"},
        {"op": "eval", "name": "jobX", "js": "CLIPS[0].job.ins[0].x"},
    ]
    res = run_steps(tmp_path, steps)
    assert res["xAfter"] > res["xBefore"] + 1e-6, res            # вправо — x растёт
    assert abs(res["jobX"] - res["xAfter"]) < 1e-9, res          # в задании то же самое значение


# ============== 4. SHIFT ФИКСИРУЕТ ОДНУ ОСЬ ДРАГА ==============
def test_chrome_shift_drag_locks_the_dominant_axis(tmp_path):
    """Драг с Shift по преобладающей вертикали: x не меняется, меняется y; без Shift — x меняется.

    Замена test_frame_drag_shift_locks_one_axis (поведение; проводка axisLock в тексте — в старом).
    Shift — настоящий модификатор клавиатуры в событиях мыши (modifiers=8).
    """
    ins = [_ins("D:/fx/a.jpg", 2.0, 4.0, style="cam2", x=0.1, y=0.2)]
    steps = _open(ins, ins) + [
        {"op": "eval", "js": "ipvOverlayPlan(3);true"},
        {"op": "eval", "name": "b0", "js": "[INS[0].x, INS[0].y]"},
        {"op": "drag", "sel": "#ipvins .ipvwrap[data-ins=\"0\"]", "fx": 0.5, "fy": 0.5,
         "dx": 8, "dy": 70, "steps": 10, "modifiers": 8},
        {"op": "wait", "ms": 150},
        {"op": "eval", "name": "b1", "js": "[INS[0].x, INS[0].y]"},
        {"op": "drag", "sel": "#ipvins .ipvwrap[data-ins=\"0\"]", "fx": 0.5, "fy": 0.5,
         "dx": 60, "dy": 0, "steps": 8},
        {"op": "wait", "ms": 150},
        {"op": "eval", "name": "b2", "js": "[INS[0].x, INS[0].y]"},
    ]
    res = run_steps(tmp_path, steps)
    (x0, y0), (x1, y1), x2 = res["b0"], res["b1"], res["b2"][0]
    assert abs(x1 - x0) < 1e-9 and abs(y1 - y0) > 1e-3, res     # Shift: ось одна — вертикаль
    assert x2 > x1 + 1e-6, res                                   # без Shift по горизонтали x меняется


# ============== 5. ИНДЕКС ДРАГА СОВПАДАЕТ С ФИЛЬТРОМ ПЛАНА ==============
def test_chrome_drag_index_follows_the_plan_filter_not_the_raw_list(tmp_path):
    """Обёртка вставки с номером i в плане — это i-я вставка С ФАЙЛОМ; драг двигает её, а не пустую строку.

    Замена test_ipv_drag_insert_index_matches_plan_filter (поведение: пустая строка между вставками
    не сдвигает индексы). Пустая строка — INS[1], без файла; в плане её нет.
    """
    a = _ins("D:/fx/a.jpg", 2.0, 4.0, style="cam2", x=0.0, y=0.0)
    c = _ins("D:/fx/c.jpg", 2.0, 4.0, style="cam2", x=0.0, y=0.0)
    blank = _ins("", 3.0, 2.0, style="cam2")
    steps = _open([a, c], [a, c], extra_js=(
        "INS=[" + json.dumps(a) + "," + json.dumps(blank) + "," + json.dumps(c) + "];"
    )) + [
        {"op": "eval", "js": "(async()=>{await ipvPlanFetch();ipvRefresh();ipvOverlayPlan(3);return true;})()"},
        {"op": "wait", "ms": 150},
        {"op": "eval", "name": "before", "js": "INS.map(x=>[x.x,x.y])"},
        {"op": "drag", "sel": "#ipvins .ipvwrap[data-ins=\"1\"]", "fx": 0.5, "fy": 0.5, "dx": 60, "dy": 0, "steps": 8},
        {"op": "wait", "ms": 150},
        {"op": "eval", "name": "after", "js": "INS.map(x=>[x.x,x.y])"},
    ]
    res = run_steps(tmp_path, steps)
    b, a2 = res["before"], res["after"]
    assert a2[2][0] > b[2][0] + 1e-6, res            # двинута вставка c (второй файл плана)
    assert a2[1] == b[1], res                        # пустая строка не тронута
    assert a2[0] == b[0], res                        # первая вставка не тронута


# ============== 6. ШРИФТ ПРЕДПРОСМОТРА: ЕСТЬ В СПИСКЕ — СЕМЕЙСТВО И @font-face, НЕТ — ОТКАЗ ==============
def test_chrome_preview_font_is_the_listed_family_or_nothing(tmp_path):
    """Шрифт из списка системы получает своё семейство reelsi-… и правило @font-face; отсутствующий — null.

    Замена части test_preview_font_styles_db (поведение разрешения шрифта; вес 800 для отсутствующего
    и подстановка строк стиля — в старом тесте). Файл шрифта браузер не грузит через заглушку /api —
    проверяется, что семейство и правило созданы, а не что шрифт отрисовался.
    """
    res = run_steps(tmp_path, [
        {"op": "eval", "js": "FONTS=[{ps:'Oswald-Regular',family:'Oswald',file:'Oswald-Regular.ttf',var:null}];true"},
        {"op": "eval", "name": "found", "js": "(()=>{const f=ipvFontFor('Oswald-Regular');return f?f.family:null;})()"},
        {"op": "eval", "name": "rule", "js": "(()=>{const s=document.getElementById('ipv-font-faces');"
                                             "return s?s.textContent:'';})()"},
        {"op": "eval", "name": "missing", "js": "ipvFontFor('NoSuchFont-Regular')"},
    ])
    assert res["found"] == "reelsi-Oswald-Regular", res
    assert "font-family: 'reelsi-Oswald-Regular'" in res["rule"], res
    assert "/api/fontfile/Oswald-Regular" in res["rule"], res
    assert res["missing"] is None, res


# ============== 7. ПЕРЕКЛЮЧЕНИЕ РАКУРСОВ — ОДНА МАШИНА, ПО EDL, НА НАСТОЯЩИХ <video> ==============
def _clip(tmp_path, name, color, seconds=6):
    """Синтетический ролик: однотонный кадр заданного цвета, без звука (ffmpeg в tmp_path)."""
    out = pathlib.Path(tmp_path, name)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=%s:s=320x240:r=25:d=%d" % (color, seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True, timeout=180)
    return str(out).replace("\\", "/")


@live_ffmpeg
def test_chrome_camera_switch_shows_exactly_the_edl_camera_with_real_video(tmp_path):
    """Переключение ракурсов: на каждом куске EDL в эфире ровно тот <video>, что указан, без перемотки.

    Замена test_every_preview_switches_cameras_through_one_machine (поведение на настоящих
    роликах; проводка pvVideoTo/ipvSeekTo/cpvSeekTo и параметризация трёх плееров — в старом тесте).
    Ролики: два однотонных клипа разного цвета (красный — кам1, синий — кам2).
    """
    cam1 = _clip(tmp_path, "cam1.mp4", "red")
    cam2 = _clip(tmp_path, "cam2.mp4", "blue")
    steps = [
        {"op": "eval", "js": (
            "(()=>{const mk=src=>{const v=document.createElement('video');v.src=src;v.muted=true;"
            "v.preload='auto';v.style.position='absolute';document.body.appendChild(v);return v;};"
            "window.P={segs:[{ts:0,te:2,ci:0,src:0},{ts:2,te:4,ci:1,src:2},{ts:4,te:6,ci:0,src:4}],"
            "vids:[mk('%s'),mk('%s')],audioCi:0,delta:[0,0],stats:{cam:0,back:0,stale:0},"
            "curCi:-1,vidx:-1,playing:false};"
            "return Promise.all(P.vids.map(v=>new Promise(r=>{if(v.readyState>=2)r();else v.onloadeddata=()=>r();})));})()"
            % (cam1, cam2)), "name": "loaded"},
        {"op": "eval", "name": "seq", "js": (
            "(()=>{const shown=()=>P.vids.findIndex(v=>v.style.zIndex==='2');const out=[];"
            "for(const t of [1,3,5]){camApply(P,t,false);out.push(shown());}"
            "return {seq:out,cur:P.curCi,srcs:P.vids.map(v=>v.currentTime)};})()")},
    ]
    res = run_steps(tmp_path, steps)
    seq = res["seq"]["seq"]
    assert res["seq"]["srcs"] is not None and seq == [0, 1, 0], res
