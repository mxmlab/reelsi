# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Движок рендера: After Effects или встроенный (без AE).

Второй движок «Собрать и отрендерить»: тот же набор клипов, та же папка вывода, та же
очередь этапов и то же окно прогресса — отличается только то, КТО рисует ролик.

1. **Выбор доезжает до диспетчера.** `engine=builtin` в теле `/api/render_run` уходит в
   `run_render_job` и оттуда — в `run_render_builtin`; без него путь прежний (AE).
2. **Встроенный идёт в `core.webrender`, а не в aerender.** Ни `find_ae`, ни `run_proc`
   на этом пути не зовутся вовсе: AE не нужен.
3. **Очередь и папка — те же.** Несколько клипов — одна очередь (клип за клипом), файлы
   ложатся в папку вывода рендера; на клип своя строка прогресса, отмена — «Стоп».
4. **Захват под общим замком видеокарты — по кодеку.** Под NVENC `core.gpulock.gpu_lock`
   берётся на время рендера клипа — как GPU-участки нарезки (тяжёлый джоб один). Quick
   Sync, AMF и «Процессор» карту NVIDIA не занимают и замка не берут: пары
   «запекание голоса + X» проверяет tests/test_gpulock_codec.py.
5. **Выбор сохраняется и читается** — как остальные настройки рендера (`ui_state`).
6. **Подготовка задания — в ядре.** `render_job.prepare_render_task`: движок (пусто — AE,
   чужое — отказ), папка вывода (создаётся сразу), адрес запущенного сервера и заголовок
   задания в журнале. Роут только вынимает строки из тела запроса; адрес он собирает из
   СВОЕГО порта (`local_ui_port`), а не из заголовка `request.host` — заголовок
   подставляет кто угодно.

Запуск: python -m pytest tests/test_webrender_engine.py -q
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import render_job  # noqa: E402
from core.render_job import RenderJob  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

import api  # noqa: E402  (порт сервера — api._core.local_ui_port)


@pytest.fixture()
def xml_clip(tmp_path):
    """Клип с субтитрами (тот же фикстур, что у соседних тестов плана)."""
    dst = str(tmp_path / "clip.xml")
    with gzip.open(os.path.join(HERE, "fixtures", "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _job() -> RenderJob:
    return RenderJob({"running": True, "done": False, "log": [], "pct": None, "cur": "",
                      "ae": "", "out_dir": "", "result": [], "failed": [], "cancel": False,
                      "items": [], "eta": None, "eta_phase": None, "eta_total": None,
                      "eta_preliminary": False, "stage_label": None, "stage_done": 0,
                      "stage_total": 0})


def _lines(job: RenderJob) -> str:
    """Лог джоба строкой: записи бывают и словарями ({t, v}), и строками."""
    out = []
    for e in job["log"]:
        if isinstance(e, dict):
            tpl, v = e.get("t", ""), e.get("v") or {}
            try:
                out.append(tpl % v if v else tpl)
            except (TypeError, ValueError):
                out.append(str(tpl))
        else:
            out.append(str(e))
    return "\n".join(out)


def _norm(xml: str) -> dict:
    """Один клип набора в том виде, в каком его отдаёт `_norm_build_jobs` (для диспетчера)."""
    return {"xml_path": xml, "music": None, "music_dir": None, "highlights": [],
            "hl_breaks": [], "hl_count": [], "hl_joins": [], "inserts": [], "intro": [],
            "intro_remove": [], "intro_splits": [], "ncams": None, "exposure": 0.0,
            "intro_mode": "word", "roto": False, "roto_bottom": 0.0, "roto_device": None,
            "style": None, "music_db": -20.0, "music_random": False, "censor_audio": True,
            "glitch_glow": "builtin", "include_xml_inserts": False}


# --------------------------------------------------------------------------- #
# 1. Диспетчер: какой движок — такой путь
# --------------------------------------------------------------------------- #
def test_builtin_engine_goes_to_webrender_not_to_aerender(xml_clip, tmp_path, monkeypatch):
    """`engine='builtin'` — рендер идёт встроенным: AE не ищут и aerender не запускают."""
    job = _job()
    calls = {"webrender": [], "find_ae": 0, "run_proc": 0, "gpu": []}
    import core.webrender as webrender

    def fake_render(xml, out, **kw):
        calls["webrender"].append({"xml": xml, "out": out, **kw})
        open(out, "wb").write(b"mp4")
        return {"ok": True, "out": out, "frames": 10, "fps": 60, "w": 1080, "h": 1920}

    monkeypatch.setattr(webrender, "render", fake_render)
    monkeypatch.setattr(render_job, "find_ae", lambda: calls.__setitem__("find_ae", calls["find_ae"] + 1) or None)
    monkeypatch.setattr(render_job, "run_proc", lambda *a, **k: calls.__setitem__("run_proc", calls["run_proc"] + 1) or 0)
    render_job.run_render_job(job, [_norm(xml_clip)], None, str(tmp_path), engine="builtin",
                              host="127.0.0.1:5099")

    assert calls["webrender"], "встроенный движок не позвал core.webrender.render"
    assert calls["find_ae"] == 0, "встроенный рендер искал After Effects"
    assert calls["run_proc"] == 0, "встроенный рендер запускал процесс AE"
    call = calls["webrender"][0]
    assert call["host"] == "127.0.0.1:5099", "адрес сервера не доехал до встроенного рендера"
    assert call["xml"] == xml_clip, call
    assert call["out"].endswith("clip.mp4"), call      # папка вывода рендера, имя клипа
    assert os.path.dirname(call["out"]) == str(tmp_path), call
    assert callable(call["cancel"]), "у встроенного рендера нет двери отмены"
    assert job["result"], "готовый файл не попал в результат"
    assert "Рендер без AE" in _lines(job), _lines(job)


def test_default_engine_stays_after_effects(xml_clip, tmp_path, monkeypatch):
    """Без `engine` путь прежний: AE-ветка (одиночный рендер), встроенный не зовётся."""
    job = _job()
    calls = {"single": 0, "builtin": 0}
    monkeypatch.setattr(render_job, "run_render_single",
                        lambda *a, **k: calls.__setitem__("single", calls["single"] + 1))
    monkeypatch.setattr(render_job, "run_render_builtin",
                        lambda *a, **k: calls.__setitem__("builtin", calls["builtin"] + 1))
    render_job.run_render_job(job, [_norm(xml_clip)], None, str(tmp_path))
    assert calls == {"single": 1, "builtin": 0}, calls


def test_builtin_keeps_one_queue_for_many_clips(xml_clip, tmp_path, monkeypatch):
    """Несколько клипов — ОДНА очередь: клип за клипом, у каждого своя строка и файл."""
    job = _job()
    clips = []
    for name in ("01_a.xml", "02_b.xml", "03_c.xml"):
        p = tmp_path / name
        shutil.copyfile(xml_clip, p)
        clips.append(_norm(str(p)))
    order = []
    import core.webrender as webrender

    def fake_render(xml, out, **kw):
        order.append(os.path.basename(out))
        open(out, "wb").write(b"mp4")
        return {"ok": True, "out": out, "frames": 1, "fps": 60, "w": 1080, "h": 1920}

    monkeypatch.setattr(webrender, "render", fake_render)
    render_job.run_render_job(job, clips, None, str(tmp_path), engine="builtin")
    assert order == ["01_a.mp4", "02_b.mp4", "03_c.mp4"], order
    assert job["result"] == [str(tmp_path / n) for n in
                             ("01_a.mp4", "02_b.mp4", "03_c.mp4")], job["result"]
    assert job["stage_total"] == 3 and job["stage_done"] == 3, job
    assert not job["failed"], job["failed"]


def test_builtin_cancel_stops_before_the_next_clip(xml_clip, tmp_path, monkeypatch):
    """«Стоп» между клипами: следующий не начинается, ожидающие помечены stopped."""
    job = _job()
    clips = []
    for name in ("01_a.xml", "02_b.xml"):
        p = tmp_path / name
        shutil.copyfile(xml_clip, p)
        clips.append(_norm(str(p)))
    import core.webrender as webrender
    seen = []

    def fake_render(xml, out, **kw):
        seen.append(out)
        open(out, "wb").write(b"mp4")
        job["cancel"] = True            # «Стоп» нажали во время первого клипа
        return {"ok": True, "out": out, "frames": 1, "fps": 60, "w": 1080, "h": 1920}

    monkeypatch.setattr(webrender, "render", fake_render)
    render_job.run_render_job(job, clips, None, str(tmp_path), engine="builtin")
    assert len(seen) == 1, seen
    stages = [it.get("stage") for it in job["items"]]
    assert "stopped" in stages, stages


def test_builtin_takes_the_shared_gpu_lock_under_nvenc(xml_clip, tmp_path, monkeypatch):
    """Под NVENC захват идёт под ОБЩИМ замком видеокарты — как GPU-участки нарезки.

    Замок берётся ПО КОДЕКУ (`core.gpulock.codec_gpu_lock`): NVENC карту занимает,
    Quick Sync и процессор — нет. Выбор кодека подменён: настоящая проба — запуск
    ffmpeg, а тест не должен зависеть от того, что стоит на машине.
    """
    import contextlib
    from core import encoders
    job = _job()
    used = []

    @contextlib.contextmanager
    def fake_lock(label="", emit=None):
        used.append(label)
        yield

    choice = encoders.Choice(family="nvidia", label=encoders.LABELS["nvidia"],
                             args=encoders.codec_args("hevc_nvenc", "master"))
    monkeypatch.setattr(encoders, "pick",
                        lambda purpose, setting=None, prober=None: choice)

    import core.gpulock as gpulock
    import core.webrender as webrender
    monkeypatch.setattr(gpulock, "gpu_lock", fake_lock)
    monkeypatch.setattr(webrender, "render",
                        lambda xml, out, **kw: (open(out, "wb").write(b"mp4"),
                                                {"ok": True, "out": out, "frames": 1,
                                                 "fps": 60, "w": 1080, "h": 1920})[-1])
    render_job.run_render_job(job, [_norm(xml_clip)], None, str(tmp_path), engine="builtin")
    assert used, "встроенный рендер не взял общий замок GPU под NVENC"


def test_builtin_failure_is_reported_per_clip(xml_clip, tmp_path, monkeypatch):
    """Ошибка встроенного рендера — клип в failed с причиной, очередь продолжается."""
    job = _job()
    clips = []
    for name in ("01_a.xml", "02_b.xml"):
        p = tmp_path / name
        shutil.copyfile(xml_clip, p)
        clips.append(_norm(str(p)))
    import core.webrender as webrender

    def fake_render(xml, out, **kw):
        if out.endswith("01_a.mp4"):
            raise RuntimeError("съёмщик упал")
        open(out, "wb").write(b"mp4")
        return {"ok": True, "out": out, "frames": 1, "fps": 60, "w": 1080, "h": 1920}

    monkeypatch.setattr(webrender, "render", fake_render)
    render_job.run_render_job(job, clips, None, str(tmp_path), engine="builtin")
    assert [f["name"] for f in job["failed"]] == ["01_a"], job["failed"]
    assert job["result"] == [str(tmp_path / "02_b.mp4")], job["result"]


# --------------------------------------------------------------------------- #
# 2. Сервис моделей выгружается ДО старта рендера — на обоих движках
# --------------------------------------------------------------------------- #
def _shutdown_spy(monkeypatch: pytest.MonkeyPatch, calls: list[str],
                  alive: bool = True) -> None:
    """Подменить ответ сервиса моделей и записать порядок вызовов."""
    from core import model_service

    def fake() -> bool:
        calls.append("shutdown")
        return alive

    monkeypatch.setattr(model_service, "shutdown", fake)


def test_ae_рендер_выгружает_сервис_до_старта(xml_clip, tmp_path, monkeypatch):
    """AE-путь: `shutdown` сервиса моделей идёт ДО запуска рендера — и после него живых нет.

    Рендеру модели не нужны, а видеопамять нужна ему целиком (на Windows переполнение
    VRAM вешает машину). Мутации: убрать вызов `model_service_shutdown` из
    `run_render_job` — сервис уехал бы в рендер живым; позвать его ПОСЛЕ развилки
    (внутри веток) — для набора из одного клипа порядок всё равно был бы верным, а вот
    для встроенного движка пришлось бы дублировать вызов, и тест на него это ловит.
    """
    job = _job()
    calls: list[str] = []
    _shutdown_spy(monkeypatch, calls)

    def single(*a, **k):
        calls.append("render")

    monkeypatch.setattr(render_job, "run_render_single", single)
    monkeypatch.setattr(render_job, "run_render_builtin",
                        lambda *a, **k: calls.append("builtin"))

    render_job.run_render_job(job, [_norm(xml_clip)], None, str(tmp_path))
    assert calls == ["shutdown", "render"], calls
    assert "выгружен" in _lines(job), _lines(job)


def test_встроенный_рендер_выгружает_сервис_до_старта(xml_clip, tmp_path, monkeypatch):
    """Встроенный рендер (без AE): `shutdown` тоже ДО старта, и ровно один раз.

    Ровно один раз — потому что `run_render_builtin` гонит клипы ОДНОЙ очередью:
    второй вызов на клип был бы лишней просьбой к уже погашенному сервису.
    """
    job = _job()
    calls: list[str] = []
    _shutdown_spy(monkeypatch, calls)
    import core.webrender as webrender

    def fake_render(xml, out, **kw):
        calls.append("render")
        open(out, "wb").write(b"mp4")
        return {"ok": True, "out": out, "frames": 10, "fps": 60, "w": 1080, "h": 1920}

    monkeypatch.setattr(webrender, "render", fake_render)
    clips = []
    for name in ("01_a.xml", "02_b.xml"):
        p = tmp_path / name
        shutil.copyfile(xml_clip, p)
        clips.append(_norm(str(p)))

    render_job.run_render_job(job, clips, None, str(tmp_path), engine="builtin")
    assert calls == ["shutdown", "render", "render"], calls


def test_ошибка_выгрузки_не_отменяет_рендер(xml_clip, tmp_path, monkeypatch):
    """Ошибка/отказ выгрузки сервиса — строка в журнале, а не провал рендера.

    Сервиса могло не быть вовсе (`shutdown` → False), а чужой процесс мог не
    отозваться вовсе (исключение). Рендер обязан пойти: карту в худшем случае займёт
    чужой процесс, и он упадёт сам, с честной причиной.
    """
    from core import model_service

    job = _job()
    calls: list[str] = []
    _shutdown_spy(monkeypatch, calls, alive=False)
    monkeypatch.setattr(render_job, "run_render_single",
                        lambda *a, **k: calls.append("render"))
    render_job.run_render_job(job, [_norm(xml_clip)], None, str(tmp_path))
    assert calls == ["shutdown", "render"], calls
    assert "выгружен" not in _lines(job), "о неудачной выгрузке сказали как об успешной"

    def boom() -> bool:
        raise OSError("сервис не отвечает")

    monkeypatch.setattr(model_service, "shutdown", boom)
    job = _job()
    render_job.run_render_job(job, [_norm(xml_clip)], None, str(tmp_path))
    assert "не выгрузился" in _lines(job), _lines(job)


# --------------------------------------------------------------------------- #
# 3. Подготовка задания: движок, папка вывода, адрес сервера
# --------------------------------------------------------------------------- #
def test_prepare_task_defaults_to_after_effects(tmp_path):
    """Без `engine` — прежний AE-путь, папка вывода создана, подпись журнала прежняя."""
    out = tmp_path / "exp"
    task = render_job.prepare_render_task("", str(out), "", "127.0.0.1:5098")
    assert task.engine == "ae", task
    assert task.render_dir == str(out) and out.is_dir(), task
    assert task.host == "127.0.0.1:5098", task
    assert task.title == "Рендер AE", task


def test_prepare_task_takes_builtin_and_creates_the_folder(tmp_path):
    """`builtin` (в любом регистре) — встроенный движок, своя подпись, папка создана."""
    out = tmp_path / "render" / "out"          # папки ещё нет — её создаёт подготовка
    task = render_job.prepare_render_task("  BuiltIn ", str(out), ' "C:\\tmp\\jsx" ',
                                          "localhost:5098")
    assert task.engine == "builtin", task
    assert out.is_dir(), "папка вывода не создана"
    assert task.outdir == "C:\\tmp\\jsx", task
    assert task.host == "localhost:5098", task
    assert task.title == "Рендер без AE", task


def test_prepare_task_defaults_the_output_folder(tmp_path, monkeypatch):
    """Пустая папка вывода — `default_render_dir()`: та же, что отдаёт статус рендера."""
    monkeypatch.setattr(render_job, "default_render_dir", lambda: str(tmp_path / "def"))
    task = render_job.prepare_render_task("ae", "", "", "")
    assert task.render_dir == str(tmp_path / "def"), task
    assert (tmp_path / "def").is_dir(), task


def test_prepare_task_rejects_an_unknown_engine(tmp_path):
    """Неизвестный движок — отказ с кодом перевода, а не молчаливый переход на AE."""
    with pytest.raises(ReelsiError) as e:
        render_job.prepare_render_task("встроенный", str(tmp_path / "exp"), "", "")
    assert e.value.code == "render_engine_bad", e.value
    assert e.value.vars.get("engine") == "встроенный", e.value.vars


# --------------------------------------------------------------------------- #
# 4. Роут: выбор движка и его хранение
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path):
    os.environ.setdefault("REELSI_UI_STATE", str(tmp_path / "ui_state.test.json"))
    import webui
    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def _start(client, xml, engine=None, monkeypatch=None, render_dir=None):
    """Позвать /api/render_run, подменив запуск потока: проверяем разбор тела.

    Папку вывода тест задаёт САМ (`render_dir`, обычно `tmp_path`): роут её создаёт, а
    папка по умолчанию — `exp` рядом с репозиторием, и на CI это `/exp` — в корне ФС
    писать нечего, роут честно отвечает «не создать папку вывода».
    """
    import api.render as ar
    seen = {}

    class FakeThread:
        def __init__(self, target=None, args=(), kwargs=None, **k):
            seen["target"] = target
            seen["args"] = args

        def start(self):
            seen["started"] = True

    monkeypatch.setattr(ar.threading, "Thread", FakeThread)
    body = {"jobs": [{"xml": xml, "censor": True, "inserts": [], "highlights": []}],
            "render_dir": render_dir or str(ar.default_render_dir())}
    if engine is not None:
        body["engine"] = engine
    d = client.post("/api/render_run", json=body).get_json()
    ar.RJOB.update(running=False, done=False, items=[], result=[], failed=[])
    return d, seen


def test_route_passes_the_engine_and_the_server_port(client, xml_clip, tmp_path, monkeypatch):
    """`engine` и адрес сервера уезжают в диспетчер: адрес — из СВОЕГО порта."""
    d, seen = _start(client, xml_clip, "builtin", monkeypatch, render_dir=str(tmp_path / "exp"))
    assert d.get("ok"), d
    args = seen.get("args") or ()
    assert args[4] == "builtin", args          # engine — пятый аргумент диспетчера
    assert args[5], "адрес сервера не доехал"
    assert args[5] == "127.0.0.1:%d" % api._core.local_ui_port(), args[5]


def test_route_rejects_an_unknown_engine(client, xml_clip, monkeypatch):
    """Неизвестный движок — ошибка, а не молчаливый AE."""
    d, _ = _start(client, xml_clip, "встроенный", monkeypatch)
    assert d.get("error"), d
    assert d.get("err") == "render_engine_bad", d
    assert d["err_vars"]["engine"] == "встроенный", d


def test_route_and_state_keep_the_engine(client, xml_clip, monkeypatch):
    """Выбор движка живёт в ui_state: что положили — то и прочитали."""
    state = {"rendengine": "builtin", "CLIPS": []}
    r = client.post("/api/ui_state", json={"state": state}).get_json()
    assert r.get("ok"), r
    back = client.get("/api/ui_state").get_json()
    assert (back.get("state") or {}).get("rendengine") == "builtin", back


def test_status_reports_the_engine(client, monkeypatch):
    """`/api/render_status` отдаёт движок джоба: по нему фронт подписывает окно."""
    import api.render as ar
    ar.RJOB.update(engine="builtin")
    try:
        d = client.get("/api/render_status").get_json()
        assert d.get("engine") == "builtin", d
    finally:
        ar.RJOB.update(engine="ae")


def test_webui_state_file_round_trip(tmp_path, client):
    """Состояние интерфейса (в нём и выбор движка) читается и пишется через роут."""
    payload = {"state": {"rendengine": "ae", "aerender": str(tmp_path / "out")}}
    assert client.post("/api/ui_state", json=payload).get_json().get("ok")
    got = client.get("/api/ui_state").get_json()
    assert got["state"]["aerender"] == str(tmp_path / "out"), got
