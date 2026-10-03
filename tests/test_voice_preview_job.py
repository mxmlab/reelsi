# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Голос клипа в превью: неизменяемый файл, задание на клип, отмена, строка на кадре.

Пять вещей, каждая из которых ломается МОЛЧА (жалобы владельца, трекер s07):

1. превью играет трек из КЕША обработки (`cache_path`: имя по содержимому настроек).
   `<стем>.voice.wav` перезаписывался под тем же именем, и браузер отдавал старый
   звук: «сменил настройки — разницы не слышно, пока не переоткроешь превью»;
2. запекание — задание НА КЛИП, а не одно на сервер: клип A считается, клип B стоит
   «в очереди» со своим ходом, готовый трек A никогда не уезжает B;
3. «Стоп» реально останавливает счёт — снимает дочерний процесс шумодава по PID,
   снимает клип из очереди, не портит кеш и не мешает очереди идти дальше;
4. перемотка не включает СЫРОЙ звук камеры: пока дорожка обработанного голоса
   подключена, камера молчит и во время seek/догрузки `<audio>`;
5. ход голоса рисуется строкой на кадре превью (`.pvpxv` поверх кадра), там же, где
   прогресс прокси, а общая форма прогресса (`progOpen`) для голоса не зовётся.

Стенды с node гоняют БОЕВЫЕ функции из `static/app/60-preview.js` — в браузере эти
места не увидеть без живого сервера, клипа и плагинов.

Запуск:  py -3.10 -m pytest tests/test_voice_preview_job.py -q
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import speakers, voicefx  # noqa: E402
from core.project_file import write_project  # noqa: E402
from core.umsg import ReelsiError, umsg  # noqa: E402

PREVIEW_JS = ROOT / "static" / "app" / "60-preview.js"
CSS = ROOT / "static" / "app.css"

node = pytest.mark.skipif(not shutil.which("node"), reason="стенд требует node в PATH")


# --------------------------------------------------------------------------- #
# Общее: изоляция служб запекания и кеша (settings/сессии чужих тестов не трогаем)
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def voice_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Кеш обработки, профили спикеров и задания запекания — в tmp_path.

    Задания — глобальные словари модуля: оставленная запись клипа мешала бы
    следующему тесту («уже в очереди» там, где очередь пуста). Нить счёта, не
    дождавшаяся очереди, живёт спокойно, а брошенная (тест упал в середине) писала
    бы в чужой кеш — поэтому всех своих дожидаемся и отменяем.
    """
    from api import voicefx as apivfx
    from core import voicefx_sep

    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))
    voices = tmp_path / "speakers"
    voices.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(voices))
    monkeypatch.setattr(apivfx, "VOICEJOBS", {})
    monkeypatch.setattr(apivfx, "VOICECUR", {"xml": ""})
    monkeypatch.setattr(apivfx, "VOICELAST", {"xml": ""})
    monkeypatch.setattr(apivfx, "VOICERUN", threading.Lock())
    # Замок состояния — RLock, как в бою: `VOICEJOB` читают и под ним же.
    monkeypatch.setattr(apivfx, "VOICELOCK", threading.RLock())
    # Окружение RoFormer не проверяем и модель не ищем: тесты гоняют СВОЮ заглушку
    # шумодава, а не плагины и модели владельца (`_voice_sep.test` не трогаем вовсе).
    monkeypatch.setattr(voicefx_sep, "require", lambda engine: None)
    monkeypatch.setattr(voicefx_sep, "model_ready", lambda engine: True)
    monkeypatch.setattr(voicefx_sep, "env_ready", lambda: True)
    monkeypatch.setattr(voicefx_sep, "stamp", lambda engine: "test-stamp")
    # Нить счёта — под записью: в конце теста её надо дождаться (см. выше).
    threads: list[threading.Thread] = []

    def spawn(xml: str, src: str, fx: dict[str, Any]) -> None:
        th = threading.Thread(target=apivfx._voice_run_bake, args=(xml, src, fx),
                              daemon=True, name="test-voicefx-bake")
        threads.append(th)
        th.start()

    monkeypatch.setattr(apivfx, "_voice_spawn", spawn)
    yield tmp_path
    for xml in list(apivfx.VOICEJOBS):
        apivfx._voice_cancel(xml)
    for th in threads:
        th.join(timeout=30)


@pytest.fixture(autouse=True)
def bakers(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Тихая «считалка» вместо ffmpeg и шумодава: короткий путь render -> кеш.

    Заменяется ровно транспорт звука (`ffmpeg` и шаги RoFormer): КЛЮЧ кеша, длина
    трека и копия рядом с XML — настоящие, их и проверяют тесты. Заглушка дочернего
    процесса умеет «мгновенно» — медленную включают точечно тесты очереди.
    """
    from core import voicefx_sep

    calls = {"run": 0, "child": 0}
    real_run = subprocess.run

    def fake_run(cmd: list[str], **kw: Any) -> Any:
        cmd = [str(c) for c in cmd]
        if not cmd or os.path.basename(cmd[0]).lower() not in ("ffmpeg", "ffmpeg.exe"):
            return real_run(cmd, **kw)      # не наш транспорт (tasklist и прочее) — как есть
        calls["run"] += 1
        out = cmd[-1]                       # ffmpeg пишет результат последним аргументом
        _touch(out)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    # Ход работы (`N/M`, как RoFormer) + готовый «сухой» файл модели.
    def fast_child(cmd: list[str], what: str, timeout: int, emit: Any = None,
                   cancelled: Any = None, progress: Any = None, pid_of: Any = None) -> Any:
        calls["child"] += 1
        if progress is not None:
            progress(5, 5)
        out_dir = str(cmd[cmd.index("--output_dir") + 1])
        _touch(os.path.join(out_dir, "dry_stub.wav"), seconds=1.0)
        return voicefx._ChildRun([], [], 0)

    monkeypatch.setattr(voicefx.subprocess, "run", fake_run)
    monkeypatch.setattr(voicefx, "_mono48", lambda src, dst, emit: _touch(dst))
    monkeypatch.setattr(voicefx, "_mix_wav",
                        lambda orig, proc, dst, percent, emit: _touch(dst))
    monkeypatch.setattr(voicefx_sep, "separated_wav",
                        lambda out_dir: _touch(os.path.join(out_dir, "dry_stub.wav")))
    monkeypatch.setattr(voicefx, "_run_child", fast_child)
    return calls


@pytest.fixture
def slow_child(monkeypatch: pytest.MonkeyPatch, bakers: Any) -> Any:
    """«Долгий» дочерний счёт: ход идёт, а «Стоп» есть что снимать — есть PID.

    Никакого чужого процесса не заводим: PID выдаём свой и им же помечаем
    `pid_of`, по которому «Стоп» и обязан снимать счёт. Настоящее убийство по PID
    проверяется отдельно (`kill_pid` на живом процессе-пустышке).
    """
    seen: dict[str, Any] = {"pid": 0, "cancelled": False, "finished": False}

    def long_child(cmd: list[str], what: str, timeout: int, emit: Any = None,
                   cancelled: Any = None, progress: Any = None, pid_of: Any = None) -> Any:
        pid = os.getpid() + 4242               # «свой» PID: другого процесса не заводим
        seen["pid"] = pid
        if pid_of is not None:
            pid_of(pid)
        out_dir = str(cmd[cmd.index("--output_dir") + 1])
        _touch(os.path.join(out_dir, "dry_stub.wav"), seconds=1.0)
        for i in range(1, 61):
            if cancelled is not None and cancelled():
                seen["cancelled"] = True
                raise ReelsiError(umsg("voicefx_cancelled", f"{what}: отменено"))
            if progress is not None:
                progress(i, 60)
            time.sleep(0.05)
        seen["finished"] = True                # «досчитал сам» — тесты этого не ждут
        return voicefx._ChildRun([], [], 0)

    monkeypatch.setattr(voicefx, "_run_child", long_child)
    return seen


def _wait_true(cond: Any, timeout: float = 10.0) -> bool:
    """Дождаться, пока условие станет истинным: шаги счёта идут по времени."""
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return bool(cond())


def _make_clip(tmp_path: Path, name: str) -> tuple[str, str]:
    """Клип для запекания: XML, сайдкар нарезки и файл камеры 1."""
    xml = tmp_path / ("%s.xml" % name)
    xml.write_text("<xmeml/>", encoding="utf-8")
    cam1 = tmp_path / ("%s_cam1.mp4" % name)
    cam1.write_bytes(b"camera-" + name.encode())
    write_project(os.path.splitext(str(xml))[0] + ".project.json",
                  {"cams": [str(cam1)], "fps": 60, "keep": [[0.0, 1.0]], "speaker": "Голос"})
    return str(xml), str(cam1)


def _flask_client() -> Any:
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def _wait_status(xml: str, *keys: str, timeout: float = 10.0) -> dict[str, Any]:
    """Дождаться, пока ход задания клипа совпадёт со всеми `keys` (или вернуть как есть)."""
    from api import voicefx as apivfx
    end = time.time() + timeout
    while True:
        with apivfx.VOICELOCK:
            slot = dict(apivfx.VOICEJOBS.get(xml) or {})
        if slot and all(slot.get(k) for k in keys):
            return slot
        if time.time() > end:
            return slot
        time.sleep(0.01)


# --------------------------------------------------------------------------- #
# 1. Превью играет трек из КЕША: имя по содержимому настроек, файл не перезаписывается
# --------------------------------------------------------------------------- #
FX_A: dict[str, Any] = {"denoise": {"on": True, "engine": "roformer", "mix": 40},
                        "vst": []}
FX_B: dict[str, Any] = {"denoise": {"on": True, "engine": "roformer", "mix": 90},
                        "vst": []}


def test_preview_voice_path_carries_the_settings_key(tmp_path: Path, bakers: Any) -> None:
    """Путь превью — из кеша по содержимому настроек, и `<стем>.voice.wav` остаётся на месте.

    Разные настройки — РАЗНЫЕ файлы (браузер видит новый URL и играет новый звук
    сразу, без переоткрытия превью), одни и те же — тот же файл (второй раз не
    считаем). `.voice.wav` при этом никуда не девается: его читают AE, DRP, XML и
    черновик, и он по-прежнему собирается из того же посчитанного трека.
    """
    c = _flask_client()
    xml, cam1 = _make_clip(tmp_path, "clip_a")
    norm_a = voicefx.normalize_fx(FX_A)
    norm_b = voicefx.normalize_fx(FX_B)

    # Один и тот же вход — один и тот же путь; другой вход — другой
    assert voicefx.cache_path(cam1, FX_A) == voicefx.cache_path(cam1, FX_A)
    assert voicefx.cache_path(cam1, FX_A) != voicefx.cache_path(cam1, FX_B), \
        "разные настройки дали один файл — смена ручки не сменит звук"

    first = c.post("/api/voicefx_bake", json={"xml": xml, "src": cam1, "fx": FX_A}).get_json()
    assert first["ok"] is True and first["running"] is True, first
    slot = _wait_status(xml, "done", timeout=15)
    assert slot.get("done") and not slot.get("error"), slot

    cache_a = voicefx.denoise_cache_path(cam1, norm_a["denoise"])
    assert slot["path"] == cache_a, "превью отдали не дорожку шумодава: %s" % slot["path"]
    assert os.path.basename(slot["path"]) != os.path.basename(voicefx.final_voice_path(xml)), \
        "превью снова играет <стем>.voice.wav — тот самый файл с постоянным именем"
    assert os.path.isfile(cache_a), "дорожка шумодава не появилась"
    # Запекание превью считает ТОЛЬКО шумодав: итоговый голос с плагинами печётся на
    # сборке, и профиль/превью его не трогают.
    assert not os.path.isfile(voicefx.final_voice_path(xml)), \
        "запекание превью испекло итоговый голос — он печётся только на выводе"
    assert not os.path.isfile(os.path.splitext(xml)[0] + ".voice.json")

    # Повторный заказ под теми же настройками — тот же (готовый) путь и ни одного счёта
    runs_before = bakers["run"]
    again = c.post("/api/voicefx_bake", json={"xml": xml, "src": cam1, "fx": FX_A}).get_json()
    assert again["ready"] is True and again["path"] == cache_a, again
    assert bakers["run"] == runs_before, "готовый трек посчитали заново"

    # Сменили силу — ДРУГОЙ путь: превью обязано получить новый URL, а не тот же
    other = c.post("/api/voicefx_bake", json={"xml": xml, "src": cam1, "fx": FX_B}).get_json()
    assert other["ready"] is False and other["queued"] is False, other
    assert other["running"] is True, other
    other_done = _wait_status(xml, "done", timeout=15)
    assert other_done["path"] == voicefx.denoise_cache_path(cam1, norm_b["denoise"]), other_done["path"]
    assert other_done["path"] != cache_a, "смена настроек отдала прежний трек"
    assert os.path.isfile(cache_a), "смена настроек перезаписала прежний трек в кеше"


def test_cache_name_is_content_based_not_overwritten(bakers: Any, tmp_path: Path) -> None:
    """Имя в кеше — ключ по содержимому настроек: старый файл живёт, новый не затирает его."""
    cam1 = str(tmp_path / "cam1.mp4")
    Path(cam1).write_bytes(b"camera")
    a = voicefx.render_cached(cam1, FX_A)
    b = voicefx.render_cached(cam1, FX_B)
    assert a != b and os.path.isfile(a) and os.path.isfile(b)
    assert a == voicefx.render_cached(cam1, FX_A), "тот же вход посчитался второй раз"
    assert bakers["run"] == 2, "лишний счёт: %s" % bakers["run"]
    # Уже посчитанный трек не переписывается: содержимое файла A не изменилось
    before = Path(a).read_bytes()
    voicefx.render_cached(cam1, FX_A)
    assert Path(a).read_bytes() == before


# --------------------------------------------------------------------------- #
# 2. Задание на клип: очередь, свой ход, чужой трек не уезжает, «Стоп»
# --------------------------------------------------------------------------- #
def test_each_clip_has_its_own_job_and_the_track_never_leaks(
        tmp_path: Path, slow_child: Any) -> None:
    """Два клипа подряд: у каждого свой ход, второй стоит «в очереди», трек A не уезжает B.

    Раньше задание было одно на сервер: `_voice_start` при другом XML перезаписывал
    слот, а первый поток, закончив, писал свой путь в ЧУЖОЙ слот — клип B получал
    трек клипа A. Теперь состояние — по `xml`, и очередь видна каждому своему клипу.
    """
    from api import voicefx as apivfx

    c = _flask_client()
    xml_a, cam_a = _make_clip(tmp_path, "clip_a")
    xml_b, cam_b = _make_clip(tmp_path, "clip_b")

    a = c.post("/api/voicefx_bake", json={"xml": xml_a, "src": cam_a, "fx": FX_A}).get_json()
    assert a["running"] is True and a["queued"] is False, a
    slot_a = _wait_status(xml_a, "i", timeout=10)
    assert slot_a.get("n", 0) > 0, "ход счёта клипа A не появился: %s" % slot_a

    # Клип B: счёт занят — B В ОЧЕРЕДИ, и ход у него СВОЙ (нулевой)
    b = c.post("/api/voicefx_bake", json={"xml": xml_b, "src": cam_b, "fx": FX_A}).get_json()
    assert b["xml"] == xml_b, "ответ B называет чужой клип: %s" % b
    assert b["queued"] is True and b["running"] is False, b
    st_b = c.get("/api/voicefx_bake_status?xml=" + _q(xml_b)).get_json()
    assert st_b["xml"] == xml_b and st_b["queued"] is True, st_b
    assert st_b["n"] == 0 and st_b["path"] == "", "B показывает ход чужого счёта: %s" % st_b
    st_a = c.get("/api/voicefx_bake_status?xml=" + _q(xml_a)).get_json()
    assert st_a["running"] is True and st_a["n"] > 0, st_a
    assert st_a["pct"] != 0 and st_a["pct"] != st_b["pct"]

    # Клип A досчитан — очередь пошла дальше, и B считает СВОИМ треком
    done_a = _wait_status(xml_a, "done", timeout=30)
    assert done_a["done"] and not done_a.get("error"), done_a
    slot_b = _wait_status(xml_b, "done", timeout=30)
    assert slot_b["done"] and not slot_b.get("error"), slot_b
    assert done_a["path"] and slot_b["path"]
    assert done_a["path"] != slot_b["path"], "клипу B уехал трек клипа A"
    assert done_a["path"] == voicefx.denoise_cache_path(cam_a, voicefx.normalize_fx(FX_A)["denoise"])
    assert slot_b["path"] == voicefx.denoise_cache_path(cam_b, voicefx.normalize_fx(FX_A)["denoise"])
    assert os.path.isfile(done_a["path"]) and os.path.isfile(slot_b["path"])
    # Трек клипа A лежит рядом и с ЕГО XML — не с чужим
    assert not os.path.isfile(voicefx.final_voice_path(xml_a))  # плагины печёт только вывод
    assert not voicefx.final_voice_ready(xml_b, cam_a, voicefx.normalize_fx(FX_A)), \
        "клип B готов под чужой файл камеры"
    with apivfx.VOICELOCK:
        assert set(apivfx.VOICEJOBS) == {xml_a, xml_b}, apivfx.VOICEJOBS


def test_cancel_stops_own_count_and_queue_goes_on(tmp_path: Path,
                                                  slow_child: Any) -> None:
    """«Стоп»: счёт клипа снимается по его PID, очередь идёт дальше, повторный счёт — заново.

    Снимается ИМЕННО свой счёт (PID — того процесса, который запустили мы), а не
    «всё по имени»: чужой клип не трогается, файлов после отмены не остаётся, и
    повторный заказ работает как первый.
    """
    c = _flask_client()
    xml_a, cam_a = _make_clip(tmp_path, "clip_a")
    xml_b, cam_b = _make_clip(tmp_path, "clip_b")

    c.post("/api/voicefx_bake", json={"xml": xml_a, "src": cam_a, "fx": FX_A})
    _wait_status(xml_a, "pid", timeout=10)
    assert slow_child["pid"] > 0, "PID счёта не доехал до задания клипа A"
    c.post("/api/voicefx_bake", json={"xml": xml_b, "src": cam_b, "fx": FX_A})

    d = c.post("/api/voicefx_bake_cancel", json={"xml": xml_a}).get_json()
    assert d["ok"] is True and d["pid"] == slow_child["pid"], \
        "«Стоп» снял не тот процесс: %s против %s" % (d, slow_child["pid"])
    # Отмена читается на КАЖДОМ шаге счёта: до него доходит не мгновенно, а в пределах шага
    assert _wait_true(lambda: slow_child["cancelled"], 10), "счёт клипа A не увидел отмену"
    assert not voicefx.final_voice_ready(xml_a, cam_a, voicefx.normalize_fx(FX_A)), \
        "отменённый счёт оставил готовый трек клипа A"
    st_a = _wait_status(xml_a, "done", timeout=15)
    assert st_a["running"] is False and st_a["error"] == "", \
        "отмена показана ошибкой, а это решение человека: %s" % st_a
    assert st_a["pid"] == 0, "после отмены задание всё ещё держит PID счёта: %s" % st_a

    # Очередь идёт дальше: B считается сам
    st_b = _wait_status(xml_b, "done", timeout=30)
    assert st_b["done"] and not st_b["error"], "после «Стоп» очередь встала: %s" % st_b
    assert st_b["path"] == voicefx.denoise_cache_path(cam_b, voicefx.normalize_fx(FX_A)["denoise"])

    # Повторный заказ клипа A считается заново и доходит до конца
    again = c.post("/api/voicefx_bake", json={"xml": xml_a, "src": cam_a, "fx": FX_A}).get_json()
    assert again["running"] is True or again["queued"] is True, again
    back = _wait_status(xml_a, "done", timeout=30)
    assert back["done"] and not back["error"], back
    assert back["path"] == voicefx.denoise_cache_path(cam_a, voicefx.normalize_fx(FX_A)["denoise"])
    assert os.path.isfile(back["path"]), "после отмены повторный счёт не дописал трек"


def test_cancel_removes_from_queue_without_touching_the_running_clip(
        tmp_path: Path, slow_child: Any) -> None:
    """Отмена клипа из ОЧЕРЕДИ: он не считается вовсе, а текущий счёт продолжается."""
    c = _flask_client()
    xml_a, cam_a = _make_clip(tmp_path, "clip_a")
    xml_b, cam_b = _make_clip(tmp_path, "clip_b")

    c.post("/api/voicefx_bake", json={"xml": xml_a, "src": cam_a, "fx": FX_A})
    _wait_status(xml_a, "pid", timeout=10)
    c.post("/api/voicefx_bake", json={"xml": xml_b, "src": cam_b, "fx": FX_A})

    d = c.post("/api/voicefx_bake_cancel", json={"xml": xml_b}).get_json()
    assert d["ok"] is True and d["pid"] == 0, "очередь снимали убийством чужого счёта: %s" % d
    st_b = c.get("/api/voicefx_bake_status?xml=" + _q(xml_b)).get_json()
    assert st_b["queued"] is False and st_b["running"] is False, st_b
    assert slow_child["cancelled"] is False, "отмена очереди сняла чужой счёт"
    # ...а счёт клипа A не тронут и доходит до конца
    slot_a = _wait_status(xml_a, "done", timeout=30)
    assert slot_a["done"] and not slot_a["error"], slot_a
    time.sleep(0.2)
    st_b = c.get("/api/voicefx_bake_status?xml=" + _q(xml_b)).get_json()
    assert st_b["done"] is False and st_b["running"] is False, \
        "отменённый из очереди клип всё равно пошёл считать: %s" % st_b
    assert not os.path.isfile(voicefx.denoise_cache_path(cam_b, voicefx.normalize_fx(FX_A)["denoise"])), \
        "отменённый счёт успел положить трек в кеш"


def test_kill_pid_never_touches_a_foreign_process() -> None:
    """`kill_pid` — только свои PID: пустой и отрицательный не трогаются вовсе.

    Настоящее убийство по PID здесь не проверить: файловый песочнице запрещено
    снимать чужие процессы (`taskkill` отвечает «Access denied»), а обходить это
    запретом на «убить по имени» — ровно то, чего делать нельзя. Поэтому проверяется
    граница: НАШ способ снимает лишь тот PID, который ему дали, и на пустом номере
    не делает ничего.
    """
    from core.jobstate import kill_pid

    kill_pid(0, "тест")            # «нет процесса» — молча, без исключения
    kill_pid(-1, "тест")


def test_cancel_unknown_clip_is_not_an_error() -> None:
    """Отмена клипа без задания — не ошибка: человек мог нажать «Стоп» дважды."""
    c = _flask_client()
    d = c.post("/api/voicefx_bake_cancel", json={"xml": "C:/out/нет.xml"}).get_json()
    assert d["ok"] is True and d["pid"] == 0, d
    e = c.post("/api/voicefx_bake_cancel", json={}).get_json()
    assert e.get("err") == "voicefx_no_xml", e


def _q(path: str) -> str:
    from urllib.parse import quote
    return quote(path, safe="")


def _touch(path: str, seconds: float = 1.0) -> str:
    """Файл-заглушка WAV: длина важна — по ней кеш и проверка готовности."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00" * int(48000 * seconds))
    return path


# --------------------------------------------------------------------------- #
# 3. Стенд плеера: перемотка не включает сырой звук; ход — строкой на кадре
# --------------------------------------------------------------------------- #
def _func_src(src: str, name: str) -> str:
    """Текст функции `name` от объявления до сбалансированной закрывающей скобки."""
    m = re.search(r"(?<![\w$])(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m is not None, f"не нашлась функция {name}"
    depth = 0
    for i in range(m.start(), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError(f"не нашлась закрывающая скобка функции {name}")


def _voice_src(*extra: str) -> str:
    """Тела всех функций дорожки (`vt*`) из боевого файла + названные соседние."""
    preview = PREVIEW_JS.read_text(encoding="utf-8")
    names = sorted(set(re.findall(r"^(?:async\s+)?function\s+(vt[A-Za-z0-9_$]*)\s*\(",
                                  preview, re.M)))
    assert names, "в 60-preview.js нет ни одной функции дорожки (vt*)"
    out = [_func_src(preview, n) for n in names]
    out += [_func_src(preview, n) for n in extra]
    return "\n".join(out)


STAND_DOM = r"""
// Мини-DOM: теги, атрибуты и разбор innerHTML — строка хода голоса строится разметкой,
// и без разбора кнопки «Стоп» в ней не найти (а `onclick` на ней — часть контракта).
const VOID_TAGS={'input':1,'br':1,'img':1,'hr':1,'meta':1,'link':1};
function _attrsOf(s){
  const out={};const rx=/([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*"([^"]*)")?/g;let m;
  while((m=rx.exec(s||'')))out[m[1]]=(m[2]===undefined?'':m[2]);
  return out;}
function _dataKey(k){return k.slice(5).replace(/-(\w)/g,(s,c)=>c.toUpperCase());}
class El{
  constructor(tag){this.tag=tag;this.attrs={};this.dataset={};this.style={};this.children=[];
    this.parentNode=null;this.src='';this.volume=1;this.readyState=0;this.paused=true;
    this.seeking=false;this.currentTime=0;this.preload='';this.muted=false;this.played=0;
    this.pauseLog=0;this.seekLog=[];this._ev={};this._text='';this._html='';
    this.onclick=null;this.id='';this.className='';}
  setAttribute(k,v){this.attrs[k]=String(v);
    if(k.indexOf('data-')===0)this.dataset[_dataKey(k)]=String(v);}
  getAttribute(k){return this.attrs[k];}
  removeAttribute(k){delete this.attrs[k];if(k==='src')this.src='';}
  appendChild(c){c.parentNode=this;this.children.push(c);return c;}
  insertBefore(c,ref){c.parentNode=this;const i=ref?this.children.indexOf(ref):-1;
    if(i<0)this.children.push(c);else this.children.splice(i,0,c);return c;}
  remove(){if(!this.parentNode)return;const s=this.parentNode.children;
    const i=s.indexOf(this);if(i>=0)s.splice(i,1);this.parentNode=null;}
  addEventListener(k,f){(this._ev[k]=this._ev[k]||[]).push(f);}
  get innerHTML(){return this._html;}
  set innerHTML(h){this._html=String(h);this.children=[];_parseInto(this,String(h));}
  get textContent(){return this._text;}
  set textContent(v){this._text=String(v);}
  _match(key){
    if(key[0]==='.')return (' '+this.className+' ').indexOf(' '+key.slice(1)+' ')>=0;
    const m=/^([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'))?$/.exec(key);if(!m)return false;
    const val=(m[2]!==undefined?m[2]:(m[3]!==undefined?m[3]:null));
    return val===null?(this.attrs[m[1]]!==undefined):(this.attrs[m[1]]===val);}
  _walk(sel){
    const key=sel.replace(/^\[|\]$/g,'');const out=[];
    const step=e=>{for(const c of e.children){if(c._match(key))out.push(c);step(c);}};
    step(this);return out;}
  querySelector(sel){return this._walk(sel)[0]||null;}
  querySelectorAll(sel){return this._walk(sel);}
  closest(){return null;}
  play(){this.played++;this.paused=false;return Promise.resolve();}
  pause(){this.paused=true;this.pauseLog++;}
  load(){}focus(){}blur(){}setPointerCapture(){}
}
function _parseInto(root,html){
  const rx=/<\/?([a-zA-Z][\w-]*)((?:\s+[^<>]*?)?)\/?>|([^<]+)/g;let m;
  const stack=[root];
  while((m=rx.exec(html))){
    if(m[3]!==undefined){const txt=m[3].trim();
      if(txt)stack[stack.length-1]._text+=txt;continue;}
    if(m[0][1]==='/'){if(stack.length>1)stack.pop();continue;}
    const el=new El(m[1]);const a=_attrsOf(m[2]);
    for(const k in a){el.attrs[k]=a[k];
      if(k.indexOf('data-')===0)el.dataset[_dataKey(k)]=a[k];}
    el.className=a['class']||'';
    if(a['id']!==undefined)el.id=a['id'];
    if(a['style']!==undefined&&a['style'].indexOf('width:')===0)
      el.style.width=a['style'].slice(6).replace(';','');
    stack[stack.length-1].appendChild(el);
    if(!VOID_TAGS[m[1].toLowerCase()]&&!/\/>$/.test(m[0]))stack.push(el);
  }
  return root;}
const MADE=[];
globalThis.document={createElement:t=>{const el=new El(t);MADE.push(el);return el;}};
"""

STAND_STATE = r"""
// Боевые константы дорожки и заглушки соседних дверей. Числа — те же, что в
// 60-preview.js: стенд проверяет поведение, а не настройку порогов.
const VT_DRIFT=0.15,VT_QUIET=400,VT_POLL=1000;
let MEDIA_VOL=1;
function voiceWiring(){}
function t(s,vars){return String(s).replace(/\{(\w+)\}/g,(m,k)=>
  (vars&&vars[k]!=null)?String(vars[k]):m);}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function uiLog(m){LOGS.push(String(m));}
const LOGS=[];
function errText(d){return (d&&(d.error||d.err))||'';}
function clipByXml(xml){return CLIPS.find(c=>c.xml===xml);}
function clipLabel(c){return (c&&(c.label||c.name))||'';}
let CLIPS=[];
let SPEAKERS={};
const PROGCALLS=[];
function progOpen(o){PROGCALLS.push(['open',(o&&o.title)||'']);}
function progItem(n,ev,x){PROGCALLS.push(['item',n,ev]);}
function progDone(m,bad){PROGCALLS.push(['done',m||'',!!bad]);}
function progUpdate(f,s){PROGCALLS.push(['update',f==null?null:f,s||'']);}
function progMini(){PROGCALLS.push(['mini']);}
function hideProg(){PROGCALLS.push(['hide']);}
function voiceFxStatus(host,text){const el=host&&host.querySelector('[data-vfx="status"]');
  if(el)el.textContent=text||'';}
function voiceFxRead(){return {denoise:{on:true,engine:'roformer',mix:100},vst:[]};}
// Плеер шага 1 (PV): стойка с кадром — как её собирает openPreview.
const STAGE=new El('div');STAGE.id='pvstage';
const SUBS=new El('div');SUBS.id='pvsub';STAGE.appendChild(SUBS);
const PANEL=new El('div');PANEL.dataset.vfxmode='panel';
const STATUS=new El('span');STATUS.setAttribute('data-vfx','status');PANEL.appendChild(STATUS);
globalThis.__byId={pvstage:STAGE,pvvoice:PANEL,pvsub:SUBS};
function $(id){return globalThis.__byId[id]||null;}
let PV={vids:[],bufs:[],segs:[],audio:[],words:[],dur:0,aidx:0,curCi:-1,scrubbing:false,
  playing:false,raf:0,xml:'',voicePanel:'pvvoice'};
"""


def _run_node(tmp_path: Path, body: str, name: str = "stand.js") -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON с последней строки."""
    src = (STAND_DOM + STAND_STATE + _voice_src("vtVoiceUse", "pvProgRow", "pvProgDrop", "pvProxyBlock",
                                    "pvProxyBox", "pvProxyStages") + "\n" + body)
    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60,
                          cwd=str(ROOT))
    assert proc.returncode == 0, f"node упал: {proc.stderr or proc.stdout}"
    lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    assert lines, "стенд ничего не напечатал"
    return json.loads(lines[-1])


@node
def test_seek_does_not_unmute_raw_camera(tmp_path: Path) -> None:
    """Перемотка: пока дорожка обработанного голоса наша, камера 1 молчит даже в seek.

    Было «muted = live && readyState>=2»: на перемотке `<audio>` падал в readyState<2,
    гейт открывался — и было слышно СЫРОЙ голос камеры. Теперь глушит сама
    подключённая дорожка (`st.on`), а короткая тишина на догоне — не беда.
    """
    body = r"""
PV.audio=[{ts:0,te:10,src:100}];
const v1=document.createElement('video'),v2=document.createElement('video');
v1.readyState=4;v1.currentTime=100;v2.readyState=4;PV.vids=[v1,v2];
PV.cams=[{path:'C:/cam1.mp4'},{path:'C:/cam2.mp4'}];
PV.xml='C:/out/01_clip.xml';PV.playing=true;
const st=vtOf(PV);st.on=true;st.path='C:/_voicefx/aaa.wav';
const el=vtEl(PV);el.src='/api/media?path=C%3A%2F_voicefx%2Faaa.wav';el.readyState=4;
vtTick(PV,3);
const playing={mute:!!PV.voiceMute,cam1:!!v1.muted,cam2:!!v2.muted};
// ПЕРЕМОТКА: <audio> догружается (readyState<2). Плеер при драге ползунка СТАВИТСЯ
// на паузу, но кадры продолжают идти — и на них камера обязана молчать: было
// `muted = live && readyState>=2`, и на этих кадрах было слышно СЫРОЙ голос камеры.
el.readyState=1;el.seeking=true;el.paused=true;PV.playing=false;PV.scrubbing=true;
vtTick(PV,5);
const seek={mute:!!PV.voiceMute,cam1:!!v1.muted,cam2:!!v2.muted,voicePaused:el.paused};
// ...и на кадре, который «играет» ровно в этот момент (гонка seek и VT_DRIFT) — тоже
PV.playing=true;vtTick(PV,5);
const seekPlaying={mute:!!PV.voiceMute,cam1:!!v1.muted,voicePaused:el.paused};
PV.playing=false;
// Пауза без дорожки (выключили обработку) возвращает звук камере
st.on=false;vtTick(PV,5);
const off={mute:!!PV.voiceMute,cam1:!!v1.muted};
// ...а слушают камеру 2 — её никто не глушит, дорожка камеры 1 молчит
st.on=true;PV.audioCi=1;vtTick(PV,5);
const cam2={mute:!!PV.voiceMute,cam2:!!v2.muted,voicePaused:el.paused,cam1:!!v1.muted};
console.log(JSON.stringify({playing:playing,seek:seek,seekPlaying:seekPlaying,off:off,cam2:cam2}));
"""
    res = _run_node(tmp_path, body)

    assert res["playing"]["mute"] is True and res["playing"]["cam1"] is True, res["playing"]
    assert res["seek"]["mute"] is True, "на перемотке гейт открылся — слышно сырой голос"
    assert res["seek"]["cam1"] is True, "камера 1 зазвучала во время seek"
    assert res["seek"]["voicePaused"] is True, "дорожка играла на паузе"
    assert res["seekPlaying"]["mute"] is True and res["seekPlaying"]["cam1"] is True, \
        "на догрузке посреди игры камера снова зазвучала: %s" % res["seekPlaying"]
    assert res["off"]["mute"] is False and res["off"]["cam1"] is False, \
        "дорожки нет — звук камеры обязан вернуться"
    assert res["cam2"]["mute"] is False and res["cam2"]["cam2"] is False, res["cam2"]
    assert res["cam2"]["voicePaused"] is True, \
        "дорожка камеры 1 играет, хотя слушают камеру 2: %s" % res["cam2"]


@node
def test_voice_progress_is_a_line_over_the_frame(tmp_path: Path) -> None:
    """Ход голоса — строкой на кадре превью, а не общей формой прогресса.

    Ход видно там же, где идёт сборка прокси (`#pvstage`), и у счёта есть «Стоп».
    `progOpen` для голоса не зовётся вовсе: оверлей поверх превью закрывал бы ровно
    то, что настраивают, а строка на кадре — нет. Готовый трек играется из КЕША:
    путь в ответе — ключ по настройкам, а не `<стем>.voice.wav` с постоянным именем.
    """
    body = r"""
PV.cams=[{path:'C:/cam1.mp4'}];PV.audio=[{ts:0,te:10,src:100}];
PV.xml='C:/out/01_clip.xml';PV.dur=10;
const v=document.createElement('video');v.readyState=4;PV.vids=[v];
const asked=[];
let polls=0;
let mid='',midIn=false;                       // строка хода ВО ВРЕМЯ счёта (готово — снята)
globalThis.fetch=async(url,opt)=>{
  const u=String(url);asked.push(u);
  if(u==='/api/voicefx_bake')
    return {json:async()=>({ok:true,ready:false,queued:false,running:true,pct:0,i:0,n:0})};
  if(u.indexOf('/api/voicefx_bake_status')===0){
    polls++;
    if(polls===2){const b=PV.vt.vtbox;mid=String(b?b.innerHTML:'');
      // строка стоит в контейнере стойки только пока идёт счёт: готово — снята
      midIn=!!(b&&b.parentNode&&b.parentNode.parentNode===STAGE);}
    return {json:async()=>(polls<3
      ?{ok:true,xml:PV.xml,queued:false,running:true,pct:polls*40,i:polls,n:5}
      :{ok:true,xml:PV.xml,queued:false,running:false,done:true,pct:100,i:5,n:5,
        path:'C:/_voicefx/deadbeef.wav'})};}
  return {json:async()=>({ok:true})};};
// Ждём условие по-настоящему: опрос хода идёт по setTimeout (VT_POLL = 1000 мс), и
// подменять таймеры нельзя — обход опроса сделал бы стенд слепым к тому, что он проверяет.
async function waitFor(cond,triesMs){
  const end=Date.now()+triesMs;
  while(Date.now()<end){
    if(cond())return true;
    await new Promise(r=>setTimeout(r,20));
  }
  return cond();}
(async()=>{
  await vtPrep(PV);
  await waitFor(()=>PV.vt.on,8000);          // счёт дошёл до готового трека
  const line=PV.vt.vtbox;
  const html=line?String(line.innerHTML):'';
  console.log(JSON.stringify({asked:asked,polls:polls,
    opened:PROGCALLS.filter(c=>c[0]==='open').map(c=>c[1]),
    on:PV.vt.on,path:PV.vt.path,src:PV.vt.el?PV.vt.el.src:'',
    lineHtml:html,mid:mid,id:line?line.id:'',inStage:midIn,
    stopShown:html.indexOf('data-vtstop')>=0,display:line?String(line.style.display):'',
    note:(STATUS.textContent||'')}));
  process.exit(0);
})();
"""
    res = _run_node(tmp_path, body)

    assert res["opened"] == [], "ход голоса снова открывает общую форму прогресса: %s" % res
    assert res["polls"] >= 3, "ход запекания не опрашивался: %s" % res
    assert res["inStage"] is True and res["id"] == "pvvoiceline", res
    assert "голос обрабатывается" in res["mid"], res["mid"]
    assert "data-vtstop" in res["mid"], "у строки хода нет кнопки «Стоп»: %s" % res["mid"]
    assert res["on"] is True and res["path"] == "C:/_voicefx/deadbeef.wav", res
    assert res["src"].startswith("/api/media?path="), res["src"]
    assert res["display"] == "none", "строка хода осталась висеть после конца счёта"
    assert res["note"] == "обработанный голос клипа готов", res["note"]


@node
def test_queued_clip_shows_its_own_line(tmp_path: Path) -> None:
    """Клип в очереди: строка говорит «в очереди» и без процента — своего хода ещё нет."""
    body = r"""
PV.cams=[{path:'C:/cam1.mp4'}];PV.audio=[{ts:0,te:10,src:100}];
PV.xml='C:/out/02_clip.xml';PV.dur=10;
const asked=[];
let polls=0;
globalThis.fetch=async(url,opt)=>{
  const u=String(url);asked.push([u,opt?JSON.parse(opt.body):null]);
  if(u==='/api/voicefx_bake')
    return {json:async()=>({ok:true,xml:PV.xml,ready:false,queued:true,running:false,pct:0,i:0,n:0})};
  if(u.indexOf('/api/voicefx_bake_status')===0){polls++;
    // Свой клип всё ещё в очереди: чужой счёт (70 %) его строку не переписывает
    return {json:async()=>({ok:true,xml:PV.xml,queued:true,running:false,pct:0,i:0,n:0})};}
  return {json:async()=>({ok:true})};};
// Ждём условие по-настоящему: опрос идёт по setTimeout (VT_POLL = 1000 мс), и
// подменять таймеры нельзя — обход опроса сделал бы стенд слепым к тому, что он проверяет.
async function waitFor(cond,triesMs){
  const end=Date.now()+triesMs;
  while(Date.now()<end){
    if(cond())return true;
    await new Promise(r=>setTimeout(r,20));
  }
  return cond();}
(async()=>{
  await vtPrep(PV);
  // Ход спрашивают у СВОЕГО клипа (url несёт `xml=...`), и не сразу: опрос идёт раз в
  // секунду — ждём первого запроса, а не «через сколько-то промисов».
  await waitFor(()=>asked.some(a=>String(a[0]).indexOf('/api/voicefx_bake_status?xml=')===0),8000);
  const line=PV.vt.vtbox;
  console.log(JSON.stringify({html:line?String(line.innerHTML):'',display:line?String(line.style.display):'',
    note:(STATUS.textContent||''),on:PV.vt.on,vtq:!!PV.vt.vtq,
    statusUrl:asked.filter(a=>a[0].indexOf('/api/voicefx_bake_status')===0).map(a=>a[0]),
    prog:PROGCALLS.length}));
  process.exit(0);
})();
"""
    res = _run_node(tmp_path, body)

    assert "в очереди" in res["html"], res["html"]
    assert "data-vtstop" not in res["html"], "у очереди предложена остановка чужого счёта"
    assert res["vtq"] is True and res["on"] is False, res
    assert res["statusUrl"] and "xml=" in res["statusUrl"][0], \
        "ход спрашивают не про свой клип: %s" % res["statusUrl"]
    assert "02_clip.xml" in res["statusUrl"][0], res["statusUrl"][0]
    assert res["prog"] == 0, "заказ голоса снова трогает общую форму прогресса"


@node
def test_stop_button_cancels_this_clip_only(tmp_path: Path) -> None:
    """«Стоп» у строки хода отменяет запекание СВОЕГО клипа и снимает строку."""
    body = r"""
PV.cams=[{path:'C:/cam1.mp4'}];PV.audio=[{ts:0,te:10,src:100}];
PV.xml='C:/out/03_clip.xml';PV.dur=10;
const calls=[];
globalThis.fetch=async(url,opt)=>{
  const u=String(url);calls.push([u,opt?JSON.parse(opt.body||'{}'):null]);
  if(u==='/api/voicefx_bake')
    return {json:async()=>({ok:true,xml:PV.xml,ready:false,queued:false,running:true,pct:10,i:1,n:10})};
  if(u==='/api/voicefx_bake_cancel')return {json:async()=>({ok:true,pid:4242})};
  return {json:async()=>({ok:true})};};
(async()=>{
  await vtPrep(PV);
  const line=PV.vt.vtbox;
  const shown=String(line.innerHTML);
  const btn=line.querySelector('[data-vtstop]');
  btn.onclick();
  for(let i=0;i<20;i++)await Promise.resolve();
  const cancels=calls.filter(c=>c[0]==='/api/voicefx_bake_cancel');
  console.log(JSON.stringify({shown:shown,btn:!!btn,
    cancels:cancels.map(c=>c[1].xml),after:String(line.innerHTML),
    display:String(line.style.display),note:(STATUS.textContent||''),
    seq:PV.vt.seq,want:PV.vt.want,vtend:!!PV.vt.vtend}));
  process.exit(0);
})();
"""
    res = _run_node(tmp_path, body)

    assert res["btn"] is True and "data-vtstop" in res["shown"], res
    assert res["cancels"] == ["C:/out/03_clip.xml"], \
        "«Стоп» отменил не свой клип: %s" % res["cancels"]
    assert res["after"] == "" and res["display"] == "none", "строка хода осталась после «Стоп»"
    assert res["vtend"] is True and res["want"] == "", res
    assert "остановлена" in res["note"], res["note"]


@node
def test_processing_off_leaves_no_line_on_the_frame(tmp_path: Path) -> None:
    """Обработка выключена: на кадре не остаётся ни строки хода, ни общей формы.

    Дверь заказа в этом случае хода не несёт (`running`/`queued` пусты), и строка
    «прошу голос клипа…», поставленная перед запросом, висела бы на кадре до
    переоткрытия превью — ровно то, на что жаловались владельцы прогресса.
    """
    body = r"""
PV.cams=[{path:'C:/cam1.mp4'}];PV.audio=[{ts:0,te:10,src:100}];
PV.xml='C:/out/04_clip.xml';PV.dur=10;
const v=document.createElement('video');v.readyState=4;PV.vids=[v];
globalThis.fetch=async(url,opt)=>({json:async()=>({ok:true,ready:false,queued:false,
  running:false,pct:0,i:0,n:0})});
(async()=>{
  await vtPrep(PV);
  const line=PV.vt.vtbox;
  console.log(JSON.stringify({opened:PROGCALLS.filter(c=>c[0]==='open').length,
    html:line?String(line.innerHTML):'',display:line?String(line.style.display):'none',
    note:(STATUS.textContent||''),on:!!PV.vt.on}));
  process.exit(0);
})();
"""
    res = _run_node(tmp_path, body)

    assert res["opened"] == 0, "выключенная обработка открыла общую форму прогресса"
    assert res["html"] == "" and res["display"] == "none", \
        "строка хода осталась висеть при выключенной обработке: %s" % res
    assert res["on"] is False, "трек подключён при выключенной обработке"
    assert res["note"] == "обработка выключена — звук камеры как есть", res["note"]


# --------------------------------------------------------------------------- #
# 4. Кадр превью: больше и по доступной высоте окна
# --------------------------------------------------------------------------- #
def test_preview_frame_is_sized_from_the_window_height() -> None:
    """`#pvstage` в окне правки: 9:16 и высота по окну, а не жёсткие 250 px.

    Прежний `flex:0 0 250px` давал 250×444 — кадр не тянулся вместе с окном и лицо
    было мельче, чем в ролике. Теперь высота считается от `100vh`, ширина — из неё
    по 9:16. Потолка ширины нет (`max-width:none`): с базовым `max-width:320px` ширина
    упиралась в 320, а высота нет — приёмка VZ поймала 315×729 при 1080p, кадр уже не
    9:16. Нижняя граница высоты — прежние 444 px: на 768p кадр не мельче, чем был.
    """
    css = CSS.read_text(encoding="utf-8")
    m = re.search(r"\.edcols \.pvstage\{([^}]*)\}", css)
    assert m, "пропало правило кадра превью в окне правки (.edcols .pvstage)"
    rule = m.group(1)
    assert "flex:0 0 250px" not in rule, "кадр снова жёстко 250 px"
    assert "100vh" in rule, "высота кадра не считается от высоты окна"
    assert "aspect-ratio:9/16" in rule, "кадр превью потерял формат клипа 9:16"
    assert "max-width:none" in rule, "ширина снова упирается в max-width кадра — не 9:16"
    assert re.search(r"width:calc\(max\(444px,.*\*\.5625\)", rule), \
        "ширина не выведена из той же высоты по 9:16"
    assert "max(444px," in rule, "на низком окне кадр мельче прежних 250×444"
    # Высота правой колонки кадр больше не распирает: она остаётся на своём месте
    assert "align-self:flex-start" in rule, rule
    # Камеры и узкие экраны не задеты: у них свои правила
    assert "@media(max-width:900px){.edcols{flex-direction:column}" in css, \
        "сломан узкий экран окна правки"
    assert (".edcols .pvstage{flex:none;max-width:280px;width:100%;height:auto;"
            "margin:0 auto}") in css, \
        "узкий экран окна правки потерял своё правило кадра"
    assert (".pvstage video{position:absolute;inset:0;width:100%;height:100%;"
            "object-fit:cover;background:#000}") in css, "правило камер .pvstage video изменено"


_PROG_STAND = r"""
// Прокси и голос идут разом на одной стойке (PV, #pvstage): что видно на кадре.
const PVPX={watch:['pvstage'],map:{},poll:0};
function progStatusFor(kind,o){return 'собираю прокси '+o.i+'/'+o.n+' '+o.pct+'%';}
PV.cams=[{path:'C:/cam1.mp4'}];PV.xml='C:/out/01_clip.xml';
const boxes=()=>STAGE.children.filter(c=>c.attrs['data-pvprog']!==undefined);
const rows=()=>{const b=boxes()[0];return b?b.children.map(c=>c.attrs['data-pvrow']):[];};
"""


@node
def test_proxy_and_voice_rows_share_one_container(tmp_path: Path) -> None:
    """Прокси и голос разом: обе строки в ОДНОМ контейнере стойки, а не два блока у низа.

    Раньше `.pvpx` и `.pvpxv` были независимыми абсолютными блоками у `bottom:0`, и
    второй накрывал первый (жалоба: «окна показа, сколько осталось, залезают друг на
    друга»). Снять одну строку — вторая остаётся; снять обе — контейнера нет совсем.
    """
    body = _PROG_STAND + r"""
const out={};
pvProxyStages({running:true,i:1,n:2,pct:30});
vtVoiceLine(PV,'голос обрабатывается',40,true);
out.both={boxes:boxes().length,rows:rows(),
  direct:STAGE.children.filter(c=>c.className==='pvpx'||c.className==='pvpxv').length,
  stop:!!boxes()[0].querySelector('[data-vtstop]'),
  proxyTxt:boxes()[0].querySelector('.pvpx_txt').textContent};
// повторный опрос прокси не плодит ни строк, ни контейнеров
pvProxyStages({running:true,i:2,n:2,pct:80});vtVoiceLine(PV,'голос обрабатывается',60,true);
out.again={boxes:boxes().length,rows:rows()};
vtVoiceLine(PV,'');
out.noVoice={boxes:boxes().length,rows:rows()};
vtVoiceLine(PV,'голос клипа: в очереди');
pvProxyStages({running:false});
out.noProxy={boxes:boxes().length,rows:rows()};
vtVoiceLine(PV,'');
out.none={boxes:boxes().length,left:STAGE.children.map(c=>c.id)};
console.log(JSON.stringify(out));
"""
    res = _run_node(tmp_path, body)

    assert res["both"]["boxes"] == 1 and res["both"]["rows"] == ["proxy", "voice"], res["both"]
    assert res["both"]["direct"] == 0, "строки снова висят на стойке отдельными блоками"
    assert res["both"]["stop"] is True, "у строки голоса нет кнопки «Стоп»"
    assert "собираю прокси 1/2" in res["both"]["proxyTxt"], res["both"]
    assert res["again"] == {"boxes": 1, "rows": ["proxy", "voice"]}, res["again"]
    assert res["noVoice"] == {"boxes": 1, "rows": ["proxy"]}, "снятие голоса убрало и прокси"
    assert res["noProxy"] == {"boxes": 1, "rows": ["voice"]}, "снятие прокси убрало и голос"
    assert res["none"]["boxes"] == 0 and res["none"]["left"] == ["pvsub"],         "после снятия обеих строк на кадре остался контейнер: %s" % res["none"]


def test_progress_container_css_stacks_rows() -> None:
    """CSS: абсолютен один контейнер, строки в потоке, клики глушит контейнер, «Стоп» жмётся."""
    css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

    def rule(sel: str) -> str:
        m = re.search(r"(?m)^" + re.escape(sel) + r"\{([^}]*)\}", css)
        assert m, f"в app.css нет правила {sel}"
        return m.group(1)

    box = rule(".pvprog")
    assert "position:absolute" in box and "bottom:0" in box, box
    assert "pointer-events:none" in box, "контейнер прогресса перехватывает клики по кадру"
    assert "flex-direction:column" in box, box
    for sel in (".pvpx,.pvpxv", ".pvpx", ".pvpxv"):
        assert "position:absolute" not in rule(sel),             f"{sel} снова абсолютный блок у низа кадра — наложение вернётся"
    assert "pointer-events:auto" in rule(".pvpx_stop"), "«Стоп» под контейнером не кликается"
