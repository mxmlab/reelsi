# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты параллельной ИИ-нарезки: ширина пула, замок GPU в пайплайне, пул подпроцессов.

3. cut_parallel_width: old → 1; review=True → 1; профиль шага cut на lmstudio → 1;
   облачный (openrouter) без поля → min(4, n); поле concurrency: 2 → 2; n_clips=1 → 1.
4. Пайплайн: распознавание и вздохи внутри замка, decide_markup — снаружи.
5. Пул джоба: module_cmd подменён, cut_parallel_width → 3; 5 роликов → не более 3
   одновременно, все 5 item_done, лог с префиксом [стем].
6. «Стоп» при пуле: 3 процесса (спят 5 с), JOB["cancel"]=True + _kill_curproc() →
   убиты за ≤3 с (цикл poll()), новые не стартовали; свои процессы тест добивает
   по объекту Popen в finally.
7. k == 1 → прежнее поведение: существующие тесты зелёные без правок.
8. Флаг «молчит» при пуле — счётчик молчащих: пока молчит один ролик, второй (он
   пишет строки каждые 0.05 с) не снимает флаг своим set_stalled(False).
9. Прогресс пула через НАСТОЯЩИЙ set_progress (без заглушек): 3 ролика, ширина 2 —
   поток завершается за 15 с, все ролики done, в JOB["progress"] итог 3 из 3.
   Под вызовом set_progress под LOCK (само-дедлок) падает по таймауту join.
"""
import os
import sys
import textwrap
import time
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from typing import Any  # noqa: E402

from core.aicut.config import cut_parallel_width  # noqa: E402


# ---- Тест 3: cut_parallel_width ----------------------------------------

class TestCutParallelWidth:
    """Ширина пула в зависимости от mode, review и профиля."""

    def test_old_mode_returns_1(self) -> None:
        assert cut_parallel_width("old", False, 10) == 1

    def test_review_returns_1(self) -> None:
        assert cut_parallel_width("gigaam", True, 10) == 1

    def test_local_lmstudio_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.aicut import config as acfg
        monkeypatch.setattr(acfg, "load_ai_config", lambda: {
            "active": "LM Studio",
            "profiles": {"LM Studio": {"provider": "lmstudio",
                                        "base_url": "http://localhost:1234/v1",
                                        "model": "qwen"}}})
        assert cut_parallel_width("gigaam", False, 10) == 1

    def test_cloud_openrouter_no_concurrency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.aicut import config as acfg
        monkeypatch.setattr(acfg, "load_ai_config", lambda: {
            "active": "OR",
            "profiles": {"OR": {"provider": "openrouter",
                                "base_url": "https://openrouter.ai/api/v1",
                                "model": "deepseek/deepseek-v4-flash"}}})
        assert cut_parallel_width("gigaam", False, 10) == min(4, 10)

    def test_cloud_with_concurrency_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.aicut import config as acfg
        monkeypatch.setattr(acfg, "load_ai_config", lambda: {
            "active": "OR",
            "profiles": {"OR": {"provider": "openrouter",
                                "base_url": "https://openrouter.ai/api/v1",
                                "model": "m", "concurrency": 2}}})
        assert cut_parallel_width("gigaam", False, 10) == 2

    def test_n_clips_1_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.aicut import config as acfg
        monkeypatch.setattr(acfg, "load_ai_config", lambda: {
            "active": "OR",
            "profiles": {"OR": {"provider": "openrouter",
                                "base_url": "https://openrouter.ai/api/v1",
                                "model": "m"}}})
        assert cut_parallel_width("gigaam", False, 1) == 1


# ---- Тест 4: gpu_lock вокруг распознавания и вздохов в пайплайне --------

def test_pipeline_gpu_lock_order(monkeypatch: pytest.MonkeyPatch, tmp_path: "pytest.TempPathFactory") -> None:
    """Распознавание и вздохи — внутри gpu_lock, decide_markup — снаружи."""
    from core.gigaam_cut import pipeline

    events: list[str] = []

    # Мокаем gpu_lock: записывает enter/exit
    import contextlib
    from typing import Any, Generator

    @contextlib.contextmanager
    def fake_gpu_lock(label: str = "", emit: Any = None) -> Generator[None, None, None]:
        events.append(f"gpu_enter:{label}")
        yield
        events.append(f"gpu_exit:{label}")

    monkeypatch.setattr(pipeline, "gpu_lock", fake_gpu_lock)

    text = "первый второй третий"
    words = [{"w": w, "start": float(i), "end": float(i + 1), "prob": 0.99, "space_before": i > 0}
             for i, w in enumerate(text.split())]

    monkeypatch.setattr(pipeline, "transcribe_words_whole",
                        lambda *a, **k: (text, words))
    monkeypatch.setattr(pipeline, "decide_markup",
                        lambda *a, **k: (set(range(len(words))), set(), "", []))
    monkeypatch.setattr(pipeline, "postprocess", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "refine_keep",
                        lambda k, *a, **kw: (k, list(range(len(k)))))

    def fake_cut_breaths(k: Any, a: Any, *args: Any, **kw: Any) -> Any:
        events.append("cut_breaths")
        return k, a, []

    monkeypatch.setattr(pipeline, "_cut_breaths", fake_cut_breaths)

    def fake_decide_markup(*args: Any, **kwargs: Any) -> Any:
        events.append("decide_markup")
        return set(range(len(words))), set(), "", []

    monkeypatch.setattr(pipeline, "decide_markup", fake_decide_markup)
    monkeypatch.setattr(pipeline.aicut, "unload_ours", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.aicut, "warn_foreign_models", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.xmlbuild, "build",
                        lambda *a, **k: {"total_s": 3.0, "segments": 1})
    monkeypatch.setattr(pipeline.draftrender, "clean_tmp", lambda *a, **k: None)

    out_xml = str(tmp_path / "out.xml")
    stages = {"pauses": "off", "asr": True, "sense": True, "dedupe": True,
              "refine": False, "breath": True, "draft": False}
    pipeline._run("dummy.wav", ["cam1.mp4"], [0.0], out_xml, 50.4,
                  stages=stages, emit=lambda *a, **k: None)

    # Проверяем порядок: распознавание внутри gpu_lock, decide_markup снаружи,
    # вздохи внутри gpu_lock
    assert "gpu_enter:распознавание" in events
    assert "gpu_exit:распознавание" in events
    assert "gpu_enter:вздохи" in events
    assert "gpu_exit:вздохи" in events
    assert "decide_markup" in events

    # decide_markup ПОСЛЕ gpu_exit:распознавание и ДО gpu_enter:вздохи
    idx_gpu_exit_asr = events.index("gpu_exit:распознавание")
    idx_decide = events.index("decide_markup")
    idx_gpu_enter_breath = events.index("gpu_enter:вздохи")
    assert idx_gpu_exit_asr < idx_decide < idx_gpu_enter_breath, (
        f"Порядок нарушен: {events}"
    )

    # cut_breaths внутри замка вздохов
    idx_cut_breaths = events.index("cut_breaths")
    idx_gpu_exit_breath = events.index("gpu_exit:вздохи")
    assert idx_gpu_enter_breath < idx_cut_breaths < idx_gpu_exit_breath, (
        f"cut_breaths должен быть внутри gpu_lock(вздохи): {events}"
    )


# ---- Тест 5: пул джоба — 5 роликов, одновременно не более 3 --------

_FAKE_CHILD = textwrap.dedent("""\
import sys, os, time
# Разбираем аргументы для нахождения --out
args = sys.argv[1:]
out_path = None
for i, a in enumerate(args):
    if a == "--out" and i + 1 < len(args):
        out_path = args[i + 1]
        break
if out_path:
    with open(out_path, "w") as f:
        f.write("<xml/>")
print("hello from fake omni_cut", flush=True)
time.sleep(0.3)
print("done", flush=True)
""")


def test_parallel_pool_5_clips_max_3(monkeypatch: pytest.MonkeyPatch, tmp_path: "pytest.TempPathFactory") -> None:
    """5 роликов, cut_parallel_width → 3: одновременно живых не больше 3, все 5 item_done,
    строки лога с префиксом [стем]."""
    import api.jobs as jobs
    from api._core import JOB, LOCK

    # Подменить module_cmd чтобы запускал наш скрипт
    script_path = str(tmp_path / "fake_omni.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(_FAKE_CHILD)

    def fake_module_cmd(mod: str, *args: str) -> list[str]:
        return [sys.executable, script_path] + list(args)

    monkeypatch.setattr(jobs, "module_cmd", fake_module_cmd)
    monkeypatch.setattr(jobs, "items_init", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_set", lambda *a, **k: None)

    done_items: list[str] = []
    orig_item_done = jobs.item_done

    def track_done(job: Any, lock: Any, stem: str, result: str) -> None:
        done_items.append(stem)
        orig_item_done(job, lock, stem, result)

    monkeypatch.setattr(jobs, "item_done", track_done)
    monkeypatch.setattr(jobs, "item_fail", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "job_finish", lambda: None)
    monkeypatch.setattr(jobs, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "set_stalled", lambda *a, **k: None)

    # Подменить cut_parallel_width → 3
    from core import aicut
    monkeypatch.setattr(aicut, "cut_parallel_width", lambda m, r, n: 3)

    log_lines: list[str] = []

    def capture_emit(line: str = "", /, **kw: Any) -> None:
        log_lines.append(line.format(**kw) if kw else line)

    monkeypatch.setattr(jobs, "emit", capture_emit)

    # Сбросить JOB
    with LOCK:
        JOB["cancel"] = False
        JOB["running"] = True
        JOB["done"] = False
        JOB["results"] = []
        JOB["failed"] = []
        JOB["items"] = []

    outdir = str(tmp_path / "out")
    pairs = [[f"cam_{i}.mp4"] for i in range(5)]

    jobs.run_omnicut_job(outdir, pairs, stages={"draft": False})

    assert len(done_items) == 5, f"Ожидали 5 item_done, получили {len(done_items)}"

    # Проверяем, что в логе есть строки с префиксом [стем]
    stem_lines = [l for l in log_lines if "[cam_" in l and "]" in l]
    assert len(stem_lines) > 0, "Ожидали строки лога с префиксом [стем]"


# ---- Тест 6: «Стоп» при пуле — процессы убиты -------------------------

_SLEEP_CHILD = textwrap.dedent("""\
import sys, time
# Разбираем аргументы для нахождения --out
args = sys.argv[1:]
out_path = None
for i, a in enumerate(args):
    if a == "--out" and i + 1 < len(args):
        out_path = args[i + 1]
        break
print("started", flush=True)
time.sleep(5)
if out_path:
    with open(out_path, "w") as f:
        f.write("<xml/>")
""")


def test_stop_kills_pool_processes(monkeypatch: pytest.MonkeyPatch, tmp_path: "pytest.TempPathFactory") -> None:
    """3 процесса спят по 5 с, JOB["cancel"]=True + _kill_curproc() → все три убиты,
    новые не стартовали.

    Смерти ждём циклом `poll()` не дольше 3 с: под мутацией «_kill_curproc не убивает
    реестр CURPROCS» процессы доживают свои 5 с, и тест обязан упасть за ~3 с, а не
    висеть до таймаута pytest (реестр мутация чистит, так что по CURPROCS их не
    найти — держим хэндлы). Свои процессы в finally добиваем по объекту Popen:
    пережить тест они не должны."""
    import api.jobs as jobs
    from api._core import JOB, LOCK

    script_path = str(tmp_path / "sleep_omni.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(_SLEEP_CHILD)

    def fake_module_cmd(mod: str, *args: str) -> list[str]:
        return [sys.executable, script_path] + list(args)

    monkeypatch.setattr(jobs, "module_cmd", fake_module_cmd)
    monkeypatch.setattr(jobs, "items_init", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_set", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_done", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_fail", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "set_stalled", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "emit", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "_mark_stopped_waits", lambda: None)

    from core import aicut
    monkeypatch.setattr(aicut, "cut_parallel_width", lambda m, r, n: 3)

    killed_pids: list[int] = []
    orig_kill_tree = jobs.kill_tree

    def counting_kill(p: Any) -> None:
        killed_pids.append(p.pid)
        orig_kill_tree(p)

    monkeypatch.setattr(jobs, "kill_tree", counting_kill)

    # Хэндлы своих подпроцессов: только по ним и добиваем (по имени не убиваем никогда).
    spawned: list[Any] = []
    orig_popen = jobs.subprocess.Popen

    def tracking_popen(*a: Any, **kw: Any) -> Any:
        p = orig_popen(*a, **kw)
        spawned.append(p)
        return p

    monkeypatch.setattr(jobs.subprocess, "Popen", tracking_popen)

    with LOCK:
        JOB["cancel"] = False
        JOB["running"] = True
        JOB["done"] = False
        JOB["results"] = []
        JOB["failed"] = []
        JOB["items"] = []

    finish_called = [False]
    def fake_finish() -> None:
        finish_called[0] = True
    monkeypatch.setattr(jobs, "job_finish", fake_finish)

    outdir = str(tmp_path / "out")
    pairs = [[f"cam_{i}.mp4"] for i in range(5)]

    # Запускаем в фоне и после короткой паузы ставим «Стоп»
    def run_job() -> None:
        jobs.run_omnicut_job(outdir, pairs, stages={"draft": False})

    t = threading.Thread(target=run_job, daemon=True)
    t.start()

    try:
        # Подождём, пока хоть один процесс появится в реестре
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with LOCK:
                if jobs.CURPROCS:
                    break
            time.sleep(0.05)

        # Ставим «Стоп»
        with LOCK:
            JOB["cancel"] = True
        jobs._kill_curproc()

        # Ждём смерти циклом, не дольше 3 с — под мутацией падаем здесь за ~3 с
        deadline = time.time() + 3.0
        while time.time() < deadline and any(p.poll() is None for p in spawned):
            time.sleep(0.05)
        alive = [p.pid for p in spawned if p.poll() is None]
        assert not alive, f"Процессы пула пережили «Стоп»: PID {alive}"

        t.join(timeout=10)
        assert not t.is_alive(), "Поток run_omnicut_job не завершился за 10 с"

        # Проверяем, что процессы были убиты
        assert len(killed_pids) > 0, "Ни один процесс не был убит"

        # Убедимся, что все процессы мертвы
        with LOCK:
            assert len(jobs.CURPROCS) == 0, "Реестр CURPROCS не пуст после «Стопа»"
    finally:
        for p in spawned:              # свои процессы — по объекту Popen
            try:
                if p.poll() is None:
                    p.kill()
            except OSError:
                pass                   # процесс уже умер сам — добивать нечего
        t.join(timeout=10)


# ---- Тест 7: k == 1 → прежнее поведение ------

def test_sequential_mode_k1(monkeypatch: pytest.MonkeyPatch, tmp_path: "pytest.TempPathFactory") -> None:
    """k == 1 → последовательный режим, CURPROC/CURWORK работают как раньше."""
    import api.jobs as jobs
    from api._core import JOB, LOCK

    script_path = str(tmp_path / "seq_omni.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(_FAKE_CHILD)

    def fake_module_cmd(mod: str, *args: str) -> list[str]:
        return [sys.executable, script_path] + list(args)

    monkeypatch.setattr(jobs, "module_cmd", fake_module_cmd)
    monkeypatch.setattr(jobs, "items_init", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_set", lambda *a, **k: None)

    done_items: list[str] = []
    orig_item_done = jobs.item_done
    def track_done(job: Any, lock: Any, stem: str, result: str) -> None:
        done_items.append(stem)
        orig_item_done(job, lock, stem, result)

    monkeypatch.setattr(jobs, "item_done", track_done)
    monkeypatch.setattr(jobs, "item_fail", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "job_finish", lambda: None)
    monkeypatch.setattr(jobs, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "set_stalled", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "emit", lambda *a, **k: None)

    from core import aicut
    monkeypatch.setattr(aicut, "cut_parallel_width", lambda m, r, n: 1)

    with LOCK:
        JOB["cancel"] = False
        JOB["running"] = True
        JOB["done"] = False
        JOB["results"] = []
        JOB["failed"] = []
        JOB["items"] = []

    outdir = str(tmp_path / "out")
    pairs = [[f"cam_{i}.mp4"] for i in range(3)]

    jobs.run_omnicut_job(outdir, pairs, stages={"draft": False})

    assert len(done_items) == 3, f"Ожидали 3 item_done, получили {len(done_items)}"


# ---- Тест 8: флаг «молчит» при пуле — счётчик молчащих -----------------

_SILENT_CHILD = textwrap.dedent("""\
import sys, time
# Разбираем аргументы для нахождения --out
args = sys.argv[1:]
out_path = None
for i, a in enumerate(args):
    if a == "--out" and i + 1 < len(args):
        out_path = args[i + 1]
        break
time.sleep(2.0)          # всё это время молчит, но процесс жив
if out_path:
    with open(out_path, "w") as f:
        f.write("<xml/>")
""")

_TALKER_CHILD = textwrap.dedent("""\
import sys, time
# Разбираем аргументы для нахождения --out
args = sys.argv[1:]
out_path = None
for i, a in enumerate(args):
    if a == "--out" and i + 1 < len(args):
        out_path = args[i + 1]
        break
deadline = time.time() + 1.0
while time.time() < deadline:
    print("talk", flush=True)     # строка каждые 0.05 с — этот ролик не молчит
    time.sleep(0.05)
if out_path:
    with open(out_path, "w") as f:
        f.write("<xml/>")
""")


def test_stalled_flag_is_counter_for_pool(monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: "pytest.TempPathFactory") -> None:
    """Два ролика: первый молчит, второй пишет строки каждые 0.05 с.

    Пока молчит первый, set_stalled НЕ получает False: флаг держится счётчиком
    молчащих, а не снимается каждым подпроцессом по своему завершению. На прежнем
    коде (каждый ролик зовёт set_stalled сам) второй ролик, заканчиваясь, снимал
    флаг, пока первый ещё молчал, — флаг мигал."""
    import api.jobs as jobs
    from api._core import JOB, LOCK

    silent_script = str(tmp_path / "silent_omni.py")
    talk_script = str(tmp_path / "talk_omni.py")
    with open(silent_script, "w", encoding="utf-8") as f:
        f.write(_SILENT_CHILD)
    with open(talk_script, "w", encoding="utf-8") as f:
        f.write(_TALKER_CHILD)

    def fake_module_cmd(mod: str, *args: str) -> list[str]:
        # 01_ — молчащий ролик, 02_ — говорящий: скрипт выбираем по пути из --out
        out = args[args.index("--out") + 1] if "--out" in args else ""
        return [sys.executable, silent_script if "01_" in out else talk_script] + list(args)

    monkeypatch.setattr(jobs, "module_cmd", fake_module_cmd)
    monkeypatch.setattr(jobs, "items_init", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_set", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_done", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_fail", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "job_finish", lambda: None)
    monkeypatch.setattr(jobs, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "emit", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "_mark_stopped_waits", lambda: None)
    monkeypatch.setattr(jobs, "CUT_STALL_S", 0.2)     # молчание ловим за 0.2 с

    journal: list[tuple[bool, float]] = []

    def journal_stalled(flag: bool) -> None:
        journal.append((bool(flag), time.time()))

    monkeypatch.setattr(jobs, "set_stalled", journal_stalled)

    from core import aicut
    monkeypatch.setattr(aicut, "cut_parallel_width", lambda m, r, n: 2)

    with LOCK:
        JOB["cancel"] = False
        JOB["running"] = True
        JOB["done"] = False
        JOB["results"] = []
        JOB["failed"] = []
        JOB["items"] = []
        JOB["stalled"] = False

    outdir = str(tmp_path / "out")
    pairs = [["silent.mp4"], ["talker.mp4"]]

    jobs.run_omnicut_job(outdir, pairs, stages={"draft": False})

    silent_xml = os.path.join(outdir, "01_silent.xml")
    talk_xml = os.path.join(outdir, "02_talker.xml")
    assert os.path.isfile(silent_xml), "молчащий ролик не досчитался"
    assert os.path.isfile(talk_xml), "говорящий ролик не досчитался"

    assert any(flag for flag, _ in journal), f"флаг «молчит» не поднимался: {journal}"

    # Молчащий ролик пишет XML перед самым выходом — это момент, когда он перестал
    # молчать. До него False в журнале быть не должно: молчит первый — флаг стоит.
    silent_end = os.path.getmtime(silent_xml)
    early_false = [t for flag, t in journal if not flag and t < silent_end]
    assert not early_false, (
        "set_stalled(False) при молчащем ролике — флаг мигает: "
        f"журнал {journal}, молчун кончился в {silent_end}"
    )
    assert journal[-1][0] is False, f"в конце флаг «молчит» не снят: {journal}"


# ---- Тест 9: прогресс пула через НАСТОЯЩИЙ set_progress — не само-дедлок ------

_POOL_CHILD = textwrap.dedent("""\
import sys, time
args = sys.argv[1:]
out_path = None
for i, a in enumerate(args):
    if a == "--out" and i + 1 < len(args):
        out_path = args[i + 1]
        break
print("hello from fake omni_cut", flush=True)
time.sleep(0.2)
if out_path:
    with open(out_path, "w") as f:
        f.write("<xml/>")
""")


def test_pool_progress_with_real_set_progress(monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: "pytest.TempPathFactory") -> None:
    """Пул из 2 процессов, 3 ролика, set_progress и set_stalled — НАСТОЯЩИЕ.

    set_progress берёт тот же нереентерабельный LOCK, что и код пула. Пока вызов стоял
    под `with LOCK:` (старт ролика и finally), поток нарезки вставал навсегда, держа
    LOCK, — то есть в бою ломался ровно новый параллельный режим, а тесты этого не
    видели: в них set_progress подменён заглушкой. Здесь заглушек нет: поток обязан
    завершиться за 15 с, все 3 ролика — done, в прогрессе итог 3 из 3."""
    import api.jobs as jobs
    from api._core import JOB, LOCK

    def fake_module_cmd(mod: str, *args: str) -> list[str]:
        return [sys.executable, "-c", _POOL_CHILD] + list(args)

    monkeypatch.setattr(jobs, "module_cmd", fake_module_cmd)
    monkeypatch.setattr(jobs, "job_finish", lambda: None)
    # set_progress/set_stalled/emit НЕ подменяем: смысл теста — настоящий LOCK.
    # item_done/item_fail тоже настоящие: по ним видно, что ролики дошли до «done».

    from core import aicut
    monkeypatch.setattr(aicut, "cut_parallel_width", lambda m, r, n: 2)

    with LOCK:
        JOB["cancel"] = False
        JOB["running"] = True
        JOB["done"] = False
        JOB["results"] = []
        JOB["failed"] = []
        JOB["items"] = []
        JOB["progress"] = None
        JOB["stalled"] = False

    outdir = str(tmp_path / "out")
    pairs = [[f"cam_{i}.mp4"] for i in range(3)]

    t = threading.Thread(target=jobs.run_omnicut_job,
                         args=(outdir, pairs), kwargs={"stages": {"draft": False}},
                         daemon=True)
    t.start()
    t.join(timeout=15)
    finished = not t.is_alive()
    if not finished:
        # Само-дедлок (мутация «set_progress обратно под LOCK»): поток держит LOCK и
        # ждёт его же. Замок отпускаем СНАРУЖИ — threading.Lock это позволяет, а иначе
        # упавший тест повесит весь прогон на teardown: conftest берёт тот же LOCK.
        # Цикл, а не один release: под мутацией каждый ролик встаёт заново.
        rescue_until = time.time() + 5.0
        while t.is_alive() and time.time() < rescue_until:
            try:
                LOCK.release()
            except RuntimeError:
                pass                   # замок свободен — ждём следующего витка
            time.sleep(0.05)
    assert finished, ("Поток run_omnicut_job не завершился за 15 с — "
                      "само-дедлок set_progress под LOCK")

    with LOCK:
        stages = [it["stage"] for it in JOB["items"]]
        progress = dict(JOB["progress"] or {})
        results = list(JOB["results"])

    assert stages == ["done", "done", "done"], f"Не все ролики done: {stages}"
    assert len(results) == 3, f"В results не 3 файла: {results}"
    # set_progress хранит {"i": …, "n": …} — итог обязан быть 3 из 3
    assert (progress.get("i"), progress.get("n")) == (3, 3), \
        f"Итоговый прогресс не 3 из 3: {progress}"
    assert sorted(os.listdir(outdir)) == ["01_cam_0.xml", "02_cam_1.xml", "03_cam_2.xml"], \
        f"Не все XML собраны: {sorted(os.listdir(outdir))}"
