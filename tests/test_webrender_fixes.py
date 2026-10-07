# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Рендер без AE: бэкенд ANGLE по платформе, зависший съёмщик, «Стоп», процент, Chrome.

Проверки сняты с живого прогона встроенного рендера (Chrome без окна, карта через
Vulkan). Ни одна из них не поднимает ни Chrome, ни ffmpeg: съёмщик, процессы и
страница подменяются заглушками (исключение — поддельный браузер в проверке гашения:
он говорит по протоколу DevTools, но браузером не является).

1. **Бэкенд ANGLE по платформе.** Жёсткий `--use-angle=d3d11` — заявка на Direct3D 11,
   которого вне Windows нет: Chrome уходил в программный SwiftShader, и кадр 1080x1920
   стоил 400-550 мс при простаивающей карте (у владельца замерено 225 мс). Бэкенд
   называет Python (`ANGLE_BY_OS`, переопределение — переменной `REELSI_ANGLE`) и
   уезжает съёмщику ключом `--angle`.
2. **Зависший браузер — ошибка, а не тишина.** Съёмщик печатает метку `#timeout`, и
   `webrender.render` по ней гасит СВОИ процессы по PID (тем же путём, что при отмене)
   и выходит `ReelsiError`: задание закрывается ошибкой, а не числится `running` с
   нулевым процентом.
3. **«Стоп» гасит и встроенный рендер.** Его дети (node-съёмщик со своим Chrome, ffmpeg
   куска, склейка) регистрируются в RJOB и убиваются тем же `kill_tree`, что у AE-ветки.
4. **Процент клипа монотонный.** В журнале ДВЕ формы строк, и `_FRAME_RE` обязан ловить
   только суммарную: сырой счётчик куска уводил полосу назад (19 % → 5 %).
5. **Chrome убирается за собой на нормальном завершении** — вместе с деревом: обёртка
   `google-chrome` оставляла жить сам браузер и его детей (155 процессов, 112 зомби).
   Съёмщик ждёт конца уборки: `taskkill /T` запускается и ДОЖИДАЕТСЯ, иначе дерево
   добивалось уже за спиной вышедшего процесса, и под нагрузкой прогона проверка
   видела живым то, что просто ещё не успели погасить. Поддельный браузер стенда
   объявляет готовность ТОЛЬКО после того, как его ребёнок записал свой порт и PID:
   прежде он поднимал DevTools сразу и съёмщик гасил дерево до появления ребёнка.
6. **Камера вне куска не роняет кусок на перемотку `<video>`**, а в лог уходит ПЕРВЫЙ
   отсутствующий адрес картинки, а не только счётчик и статус.
7. **Правило «время → кадр» совпадает на границах куска** у выемки (Python) и у страницы
   (JS): расхождение на кадр — это ДРУГАЯ картинка, а у выемки ещё и 404.

Запуск: python -m pytest tests/test_webrender_fixes.py -q
"""
import contextlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import core.webrender as wr  # noqa: E402
from core import render_job  # noqa: E402
from core.app_meta import wrap_emit  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

# Стенд покадровой отрисовки, поддельный браузер и разбор командной строки съёмщика —
# у соседнего файла: там их общий источник (мини-DOM и боевые функции 85-inserts-view.js).
# Тот же приём, что у test_cam2_zoom и test_zoom_pick_matrix: второй копии стенда быть
# не должно, иначе он разойдётся с боевым кодом молча.
from test_webrender import (CAPTURE, JS60, JS85, _FAKE_CHROME_JS,  # noqa: E402
                            _TINY_JPEG_B64, _capture_json, _func, _python_capture_cmd,
                            _read, _run_node, _stand, node)


# --------------------------------------------------------------------------- #
# 1. Бэкенд ANGLE: платформа, переменная, доезд до съёмщика
# --------------------------------------------------------------------------- #
def test_angle_backend_is_chosen_by_platform(monkeypatch):
    """`d3d11` — Windows, `vulkan` — Linux, `metal` — macOS; REELSI_ANGLE сильнее.

    Жёсткий `d3d11` — заявка на Direct3D 11, которого вне Windows не существует:
    Chrome уходил в программный SwiftShader (кадр 1080x1920 — 400-550 мс при
    простаивающей карте). Переменная — дверь на чужой драйвер: подходящий бэкенд там
    бывает свой (`gl` на старом драйвере, `swiftshader` для проверки).
    """
    monkeypatch.delenv("REELSI_ANGLE", raising=False)
    assert wr.angle_backend("Windows") == "d3d11"
    assert wr.angle_backend("Linux") == "vulkan"
    assert wr.angle_backend("Darwin") == "metal"
    # Незнакомая ОС — самый распространённый случай, как у encoders.DEFAULT_ORDER.
    assert wr.angle_backend("FreeBSD") == wr.DEFAULT_ANGLE == "d3d11"
    # Переменная сильнее платформы: задана и не пуста — берём её.
    monkeypatch.setenv("REELSI_ANGLE", "gl")
    for system in ("Windows", "Linux", "Darwin"):
        assert wr.angle_backend(system) == "gl", system
    # Пустая переменная — не переопределение: платформа остаётся главной.
    monkeypatch.setenv("REELSI_ANGLE", "   ")
    assert wr.angle_backend("Linux") == "vulkan"


@node
def test_angle_reaches_the_catcher_by_platform(tmp_path, monkeypatch):
    """Бэкенд ANGLE доезжает до съёмщика ключом `--angle` — и с платформой, и с переменной.

    Единицы и имена ключей съёмщика сверяет его РЕАЛЬНЫЙ разбор (`--parse-only`), а не
    копия условий в тесте: переименованный ключ оставил бы у съёмщика своё умолчание, и
    Linux опять рисовал бы программно.
    """
    monkeypatch.delenv("REELSI_ANGLE", raising=False)
    for system, want in (("Linux", "vulkan"), ("Windows", "d3d11"), ("Darwin", "metal")):
        monkeypatch.setattr(wr.platform, "system", lambda s=system: s)
        got = _capture_json(_python_capture_cmd(tmp_path))
        assert got["angle"] == want, (system, got)
    # Переопределение переменной едет тем же ключом.
    monkeypatch.setenv("REELSI_ANGLE", "swiftshader")
    assert _capture_json(_python_capture_cmd(tmp_path))["angle"] == "swiftshader"
    # Номер куска — тем же ключом: по нему строка прогресса куска отличается от суммы.
    cmd = _python_capture_cmd(tmp_path)
    assert "--chunk" in cmd, cmd
    cmd[cmd.index("--chunk") + 1] = "3"
    assert _capture_json(cmd)["chunk"] == 3


@node
def test_catcher_does_not_hardcode_the_windows_backend():
    """Съёмщик собирает флаг ANGLE ИЗ КЛЮЧА, а не подставляет Windows-бэкенд жёстко.

    Мутация «вернуть d3d11 жёстко» обязана красить этот тест: без него Linux снова
    уходил бы в программную отрисовку, и заметить это можно было бы только по времени
    рендера.
    """
    src = _read(CAPTURE)
    assert "'--use-angle=' + ANGLE" in src, \
        "флаг ANGLE больше не берётся из ключа --angle — платформа опять не при чём"
    assert "--use-angle=d3d11" not in src, \
        "в съёмщике снова жёсткий d3d11: на Linux Chrome уйдёт в программный SwiftShader"
    assert "const ANGLE" in src and "args.angle" in src, "ключ --angle не читается"


def test_stall_mark_is_the_same_on_both_sides():
    """Метка зависания одна на печать и на разбор: `#timeout` печатает съёмщик, ждёт Python.

    Разойдись они — рендер опять вставал бы навсегда: метка ушла бы в журнал, а главный
    поток продолжал бы ждать кадры, которых уже не будет.
    """
    assert wr.STALL_MARK == "#timeout ", wr.STALL_MARK
    src = _read(CAPTURE)
    assert "`#timeout ${CALL_TIMEOUT_MS}`" in src, "съёмщик не печатает метку зависания"


# --------------------------------------------------------------------------- #
# 2. Зависший браузер: ошибка, гашение по PID
# --------------------------------------------------------------------------- #
class _Sink:
    """Труба кодировщика вместо настоящего ffmpeg: копит байты и «закрывается»."""

    def __init__(self) -> None:
        self.buf = b""
        self.closed = False

    def write(self, b: bytes) -> None:
        self.buf += b

    def close(self) -> None:
        self.closed = True


def _render_env(tmp_path, monkeypatch, popen):
    """Обвязка `webrender.render` на заглушках: ни сервера, ни Chrome, ни ffmpeg, ни сети."""
    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(wr, "_scene_plan",
                        lambda host, body: {"fps": 30, "w": 1080, "h": 1920, "dur": 10})
    # EDL заглушкой: без него рендер не вынимает картинки камер — обвязке это и нужно.
    monkeypatch.setattr(wr, "_render_edl", lambda *a, **k: None)
    monkeypatch.setattr(wr, "_save_body", lambda host, body: "abc123")
    monkeypatch.setattr(wr, "_tmp_dir", lambda x: str(tmp_path))
    monkeypatch.setattr(wr, "pick_codec", lambda purpose: types.SimpleNamespace(
        args=["-c:v", "libx265"], label="cpu"))
    monkeypatch.setattr(subprocess, "Popen", popen)
    return str(xml), str(out)


def test_hanging_browser_is_an_error_not_silence(tmp_path, monkeypatch):
    """`#timeout` съёмщика — ошибка рендера: свои процессы погашены, ожидание кончено.

    Живой прогон: страница не ответила за 120 с, ошибка легла в журнал, а задание
    числилось `running` с нулевым процентом — ждать кадры, которых не будет, можно
    бесконечно, и «Остановить» не помогало. Здесь съёмщик печатает метку (заглушка
    вместо 120 с ожидания) и НЕ выходит: рендер обязан сам бросить ожидание, погасить
    свои процессы по PID и выйти `ReelsiError` с причиной.
    """
    killed_pids: list[int] = []
    killed: list[object] = []
    released = threading.Event()

    class Hang:
        """Поток кадров зависшего съёмщика: кадров нет и не будет."""

        def read(self, n: int) -> bytes:
            released.wait(5.0)
            return b""

    class Cap:
        def __init__(self, cmd, **k):
            self.cmd = list(cmd)
            self.pid = 4242
            self.stdout = Hang()
            self.stderr = io.BytesIO(b"#chrome-pid 4242\n#timeout 120000\n")

        def poll(self):
            return None

        def wait(self):
            return 1

        def kill(self):
            released.set()

    class Ff:
        def __init__(self, cmd, **k):
            self.cmd = list(cmd)
            self.pid = 7
            self.stdin = _Sink()
            self.stderr = io.BytesIO(b"")

        def poll(self):
            return None

        def wait(self):
            return 1

        def kill(self):
            self.stdin.close()

    def popen(cmd, **k):
        return Ff(cmd, **k) if cmd and cmd[0] == "ffmpeg" else Cap(cmd, **k)

    xml, out = _render_env(tmp_path, monkeypatch, popen)
    monkeypatch.setattr(wr, "_kill_pid", lambda pid: killed_pids.append(pid))

    def kill_tree(p):
        killed.append(p)
        p.kill()

    monkeypatch.setattr(wr, "kill_tree", kill_tree)

    with pytest.raises(ReelsiError) as exc:
        wr.render(xml, out, dur=1.0, instances=1, audio=False)

    msg = str(exc.value)
    assert "не ответил" in msg and "120" in msg, msg
    # Гашение — по PID своего Chrome и ТЕМ ЖЕ путём, что при отмене: деревом своих детей.
    assert killed_pids == [4242], killed_pids
    assert len(killed) == 2, killed          # съёмщик кадров и кодировщик куска


# --------------------------------------------------------------------------- #
# 3. «Стоп» гасит детей встроенного рендера
# --------------------------------------------------------------------------- #
def test_stop_kills_the_children_of_the_builtin_render(tmp_path, monkeypatch):
    """Дети встроенного рендера зарегистрированы в RJOB и гасятся «Стоп».

    `render_kill` знал только AfterFX/aerender, поэтому «Остановить» на рендере без AE
    не гасило НИЧЕГО: node-съёмщик, его Chrome и ffmpeg продолжали работать. Здесь
    проверяется весь путь: движок отдаёт своих детей дверью `on_child` (их кладёт туда
    `_child_registrar`), а «Стоп» — тот же `render_kill`, что зовёт `/api/cancel` —
    гасит их и ставит флаг отмены.
    """
    from core import encoders

    job = render_job.RenderJob(
        {"log": [], "items": [], "cancel": False, "result": [], "failed": [],
         "pct": None, "cur": ""},
        emit=lambda line="", **v: None)
    render_job.items_init(job, job.lock, ["clip"])
    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    render_dir = tmp_path / "exp"

    killed: list[object] = []
    monkeypatch.setattr(render_job, "kill_proc", lambda p: killed.append(p))
    monkeypatch.setattr(encoders, "pick", lambda purpose: types.SimpleNamespace(
        args=["-c:v", "libx265"], label="cpu"))
    monkeypatch.setattr("core.gpulock.codec_gpu_lock",
                        lambda *a, **k: contextlib.nullcontext())

    seen: dict[str, list[object]] = {}

    class Proc:
        def __init__(self, name: str) -> None:
            self.name = name

        def poll(self):
            return None

    def fake_render(xml_path, out, **kw):
        on_child = kw.get("on_child")
        assert on_child is not None, "движок не отдаёт своих детей — «Стопу» гасить нечего"
        for name in ("node-съёмщик", "ffmpeg куска", "склейка"):
            on_child(Proc(name))
        seen["procs"] = list(job.procs)
        # «Пользователь нажал Стоп»: ровно тот путь, что у /api/cancel.
        render_job.render_kill(job)
        with open(out, "wb") as f:
            f.write(b"mp4")
        return {"ok": False, "cancelled": True, "out": out, "frames": 0, "fps": 30,
                "w": 1080, "h": 1920}

    monkeypatch.setattr("core.webrender.render", fake_render)

    render_job.run_render_builtin(job, [{"xml_path": str(xml)}], str(tmp_path),
                                  str(render_dir))

    assert len(seen.get("procs") or []) == 3, seen
    assert killed == seen["procs"], (killed, seen)
    assert job["cancel"] is True
    # Список — на один клип: у доснятого процесса гасить нечего.
    assert job.procs == [], job.procs


# --------------------------------------------------------------------------- #
# 4. Процент клипа: две формы строк, полоса не идёт назад
# --------------------------------------------------------------------------- #
def _chunk_progress_line(chunk: int = 3, done: int = 79, count: int = 1590) -> str:
    """Строка счётчика КУСКА — из самого съёмщика, а не копией в тесте.

    Шаблон вынимается из capture.mjs и подставляется здесь: вернуть туда прежнюю форму
    «кадр a/b» — значит сломать проверку, а не разойтись с ней молча.
    """
    # Шаблон узнаётся по `${FRAMES}`: строк с «кадр» в съёмщике несколько (в том числе
    # текст ошибки про --start), и брать первую попавшуюся — значит проверять чужую.
    m = re.search(r"err\(`([^`]*\$\{FRAMES\}[^`]*)`\)", _read(CAPTURE))
    assert m, "в capture.mjs не нашлось строки прогресса кадра — проверять нечего"
    line = m.group(1)
    for var, val in (("${CHUNK}", chunk), ("${k}", chunk), ("${i + 1}", done),
                     ("${FRAMES}", count)):
        line = line.replace(var, str(val))
    assert "${" not in line, f"шаблон строки куска не разобран: {line!r}"
    return line


def _clip_job() -> render_job.RenderJob:
    return render_job.RenderJob(
        {"log": [], "items": [{"name": "clip", "stage": "render", "pct": None,
                               "path": "", "reason": ""}]},
        emit=lambda line="", **v: None)


def test_clip_percent_is_monotonic_with_two_kinds_of_frame_lines():
    """Две формы строк в одном журнале — `items[0]["pct"]` обязан не убывать.

    Живой замер процента КЛИПА: 16.7 → 36.8 → 6.0 → 25.0 → 8.8. `_FRAME_RE` подходил к
    обеим строкам одного вида «кадр N/M»: сырой счётчик куска (79 из 1590 — 5 %) и сумма
    по всем кускам (300 из 1590 — 19 %) писали в ОДНО поле, и кто напечатал последним,
    тот и выставил процент. Теперь сырая строка куска формы не имеет.
    """
    total = 1590
    job = _clip_job()
    emit = render_job._builtin_progress(job, 0)
    items = job["items"]
    # Суммарные строки печатает тот же счётчик, что в бою (`_Progress` в обёртке render).
    prog = wr._Progress(total, wrap_emit(emit))

    chunk = _chunk_progress_line(chunk=1, done=79, count=total)
    # Формы разные: сырую строку куска дверь НЕ ловит, суммарную — ловит.
    assert chunk.startswith("кусок "), (
        f"сырой счётчик куска печатается формой строки ролика: {chunk!r}")
    assert render_job._FRAME_RE.match(chunk) is None, chunk
    assert render_job._FRAME_RE.match("кадр 300/%d" % total) is not None

    pcts: list[float] = []
    for n in (300, 700, total):
        prog.add(0, n)                  # суммарная строка ролика
        pcts.append(items[0]["pct"])
        emit(chunk)                     # рядом — сырая строка отставшего куска
        pcts.append(items[0]["pct"])
    assert all(pcts[k] >= pcts[k - 1] for k in range(1, len(pcts))), pcts
    assert pcts[-1] == pytest.approx(1.0), pcts
    # Счётчик куска (5 %) процента не выставлял ни разу — иначе в ряду было бы его число.
    assert min(pcts) > 79 / total, pcts

    # Мутация: прежняя форма сырой строки процент РОНЯЕТ — значит сторож ловит возврат
    # старого контракта, а не «совпал случайно».
    old_form = "кадр %d/%d" % (79, total)
    assert render_job._FRAME_RE.match(old_form) is not None
    emit(old_form)
    assert items[0]["pct"] == pytest.approx(79 / total), items[0]["pct"]


# --------------------------------------------------------------------------- #
# 5. Chrome убирается за собой на нормальном завершении
# --------------------------------------------------------------------------- #
def _port_alive(port: int, deadline: float = 15.0) -> bool:
    """Отвечает ли порт: живой ребёнок держит сокет, убитый — нет (зомби тоже нет)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return False
        time.sleep(0.1)
    return True


def _is_zombie(pid: int) -> bool:
    """Зомби ли процесс: родителя уже нет, осталась запись в таблице процессов.

    Читается только Linux-дверь `/proc/<pid>/status` (её на других системах нет —
    вызывается после проверки платформы). Файла нет — процесса и подавно нет; файл
    не разобрался — не гадаем: пусть решает сигнал 0, он и есть настоящая проверка.
    """
    try:
        with open("/proc/%d/status" % pid, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False
    m = re.search(r"^State:\s+(\S)", text, re.MULTILINE)
    return bool(m) and m.group(1) == "Z"


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс с этим PID — по PID, а не по имени: чужие не трогаем.

    Дверь у ОС своя, как у боевого `core.webrender._kill_pid`: на Windows это
    `tasklist`, на POSIX — сигнал 0 (`os.kill(pid, 0)`, он не доставляется). Общей
    команды нет: `tasklist` вне Windows не существует, и проверка падала на Linux
    (`FileNotFoundError: 'tasklist'`) — публичный CI идёт там.

    Разные ответы POSIX означают разное, и путать их нельзя: `ProcessLookupError` —
    процесса нет, `PermissionError` — процесс есть, но чужой этому пользователю
    (считать живым честно: номер занят, а не свободен), прочие ошибки — тоже живой,
    чтобы проверка не «проходила» на неожиданном сбое.

    На Linux зомби считается мёртвым: родителя уже нет, а запись в таблице процессов
    остаётся до `wait` (`State: Z`). Под `docker --init` зомби пожинает init, но тест
    не должен зависеть от того, кто именно его пожинает.
    """
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                             capture_output=True, text=True, timeout=60, errors="replace")
        return str(pid) in (out.stdout or "")
    if _is_zombie(pid):
        return False
    try:
        os.kill(pid, 0)          # сигнал 0 не доставляется — только проверка живости
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def test_pid_alive_branches_by_platform_and_stays_honest_on_posix(monkeypatch):
    """Живость процесса: Windows — `tasklist`, POSIX — сигнал 0; ошибки не молчат.

    Проверка живости — единственное, на чём стоит тест уборки Chrome выше, а ошибка
    в ней не краснеет, а МОЛЧИТ: «процесс мёртв» — это и есть ожидаемый ответ. Поэтому
    обе двери проверяются здесь стендом, а не работой соседнего теста: публичный CI
    идёт на Linux, где раньше звался `tasklist` и падало `FileNotFoundError`.

    Windows-дверь подменяется заглушкой и на Linux: заглушка отвечает по PID, то есть
    проверяется ещё и то, что живость берётся по PID, а не по имени.
    """
    # 1. Windows: команда — `tasklist` ПО PID, и её ответ и есть ответ проверки.
    runs = []
    monkeypatch.setattr(sys, "platform", "win32")

    def fake_run(cmd, **kw):
        runs.append([str(x) for x in cmd])
        return subprocess.CompletedProcess(cmd, 0,
                                           stdout="%s  4242  чужой.exe\n" % "Образ",
                                           stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _pid_alive(4242) is True
    assert _pid_alive(7777) is False, "чужой PID назван живым: ответ разобран не по PID"
    # Фильтр разбирается по словам: аргументы команды идут и целыми, и внутри `PID eq N`.
    words = [w.lower() for cmd in runs for x in cmd for w in x.split()]
    assert "tasklist" in words and "pid" in words and "4242" in words, words
    assert not any(w in ("/im", "chrome.exe", "imagename") for w in words), \
        f"живость берётся по имени, а не по PID: {words}"

    # 2. POSIX: сигнал 0, и каждый исход — свой (иначе «мертвы» получалось бы даром).
    # `_is_zombie` — своя дверь со своим тестом, здесь он выключен: проверяется сигнал.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys.modules.get(__name__) or globals(), "_is_zombie",
                        lambda pid: False)
    pid = 424242

    def killed(pid, sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(os, "kill", killed)
    assert _pid_alive(pid) is False, "мёртвый процесс назван живым (ProcessLookupError)"

    def denied(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "kill", denied)
    assert _pid_alive(pid) is True, "чужой процесс с занятым номером назван мёртвым"

    def broke(pid, sig):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(os, "kill", broke)
    assert _pid_alive(pid) is True, "неожиданный сбой ОС выдан за «процесса нет»"

    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)))
    assert _pid_alive(pid) is True
    assert calls == [(pid, 0)], f"проверка живости шлёт не сигнал 0: {calls}"


def test_zombie_door_reads_the_process_state_of_linux(monkeypatch):
    """Зомби (`State: Z`) — мёртв, живой (`State: S`) — жив; нет файла состояния — жив.

    Под `docker --init` зомби пожинает init, но тест не должен зависеть от того, кто
    именно его пожинает: на Linux мёртвого без `wait` сигнал 0 видит ЖИВЫМ (запись в
    таблице процессов ещё есть), и уборка Chrome считалась бы неполной. `/proc` есть
    только на Linux, поэтому дверь проверяется на подставном содержимом файла —
    разбирается ровно формат `/proc/<pid>/status`.
    """
    import io as _io

    cases = (("State:\tZ (zombie)\nName:\tpython\n", True),
             ("Name:\tpython\nState:\tS (sleeping)\n", False),
             ("Name:\tpython\n", False))        # без `State` — не гадаем, решает сигнал

    for state, expected in cases:
        def fake_open(path, *a, _state=state, **kw):
            assert str(path) == "/proc/4242/status", path
            return _io.StringIO(_state)

        monkeypatch.setattr("builtins.open", fake_open)
        assert _is_zombie(4242) is expected, state

    def no_proc(path, *a, **kw):
        raise FileNotFoundError(2, "No such file or directory", path)

    monkeypatch.setattr("builtins.open", no_proc)
    assert _is_zombie(4242) is False, "нет /proc — это «нет зомби», а не падение"


# Потолок ожидания ребёнка в стенде. По истечении стенд падает ЯВНО, а не молчит:
# иначе причина («ребёнок не запустился») снова пряталась бы за уборкой съёмщика.
_CHILD_UP_CEILING_S = 20

# Готовность «браузера» — это ответ его порта DevTools: по нему съёмщик и начинает
# съёмку. Поэтому порт поднимается ТОЛЬКО после того, как ребёнок записал свой порт и
# PID. Иначе съёмщик снимал кадры и гасил дерево раньше, чем ребёнок успевал
# стартовать, и проверка «ребёнок убит» падала на гонке стенда, а не на дефекте кода.
_CHILD_GATE_JS = r"""
const CHILD_CEILING_MS = %(ceiling)d;
const childDeadline = Date.now() + CHILD_CEILING_MS;
function childIsUp() {
  try {
    const parts = readFileSync(process.env.FAKE_CHILD_PORT_FILE, 'utf8').trim().split(/\s+/);
    return parts.length >= 2 && Boolean(parts[0]) && Boolean(parts[1]);
  } catch (e) {
    return false;
  }
}
function listenWhenChildIsUp() {
  if (childIsUp()) {
    server.listen(PORT, '127.0.0.1', () => {
      process.stderr.write('#fake-port ' + server.address().port + '\n');
    });
    return;
  }
  if (Date.now() > childDeadline) {
    process.stderr.write('#fake-error ребёнок не поднялся за '
      + (CHILD_CEILING_MS / 1000) + ' с\n');
    process.exit(1);
  }
  setTimeout(listenWhenChildIsUp, 20);
}
listenWhenChildIsUp();
"""


def _stand_is_ready_when_child_is_up(js: str) -> str:
    """Поддельный браузер «готов» только после того, как ребёнок записал порт и PID.

    Гонка стенда: «браузер» стартовал дочерний процесс и СРАЗУ поднимал порт DevTools,
    по которому съёмщик начинает работу. Съёмщик снимал два кадра за миллисекунды и
    гасил дерево раньше, чем ребёнок успевал запуститься и записать свой порт, — файла
    не было, и проверка «ребёнок убит» падала как «поддельный браузер не поднял
    ребёнка». Теперь DevTools открывается по файлу ребёнка: к началу съёмки он уже
    точно жив, и проверку «ребёнок убит» ослаблять не приходится.
    """
    fs_import = "import { writeFileSync } from 'node:fs';"
    assert fs_import in js, "поддельный браузер сменил импорт node:fs — стенд устарел"
    js = js.replace(fs_import, "import { readFileSync, writeFileSync } from 'node:fs';")

    listen = ("server.listen(PORT, '127.0.0.1', () => {\n"
              "  process.stderr.write('#fake-port ' + server.address().port + '\\n');\n"
              "});")
    assert listen in js, "поддельный браузер сменил запуск сервера — стенд устарел"
    return js.replace(listen, _CHILD_GATE_JS % {"ceiling": _CHILD_UP_CEILING_S * 1000})


def test_catcher_kills_its_chrome_with_children_on_a_normal_finish(tmp_path):
    """Нормальное завершение куска убирает СВОЙ Chrome вместе с его детьми.

    Живой прогон: после рендера в контейнере осталось 155 процессов Chrome, из них 112
    зомби. `google-chrome` — обёртка-скрипт, и SIGKILL родителю оставляет жить сам
    браузер и всех его детей. Проверяется на ПОДДЕЛЬНОМ браузере (Chrome и ffmpeg не
    поднимаются): он, как настоящий, держит живой дочерний процесс — по PID ребёнка и
    видно, убит ли он вместе с родителем.

    Ребёнок слушает порт, и `_port_alive` по нему проверялся РАНЬШЕ. Проверка через
    порт видит ещё и чужого слушателя: номер освободился — его мог занять другой
    процесс (в том числе оставшийся от соседнего теста под `-n auto`), и «порт жив»
    означало «ребёнок жив» только по совпадению. PID так совпасть не может, поэтому
    смерть проверяется по PID, а порт остаётся сторожем стенда: он подтверждает, что
    ребёнок БЫЛ и держал сокет (иначе проверять нечего).

    Гонка самого стенда: «браузер» объявлял готовность сразу после запуска ребёнка, и
    съёмщик успевал погасить дерево до того, как ребёнок запишет порт. Теперь готовность
    (ответ порта DevTools) наступает только по файлу ребёнка — см.
    `_stand_is_ready_when_child_is_up`.
    """
    stand = tmp_path / "fake_chrome.mjs"
    js = _FAKE_CHROME_JS.replace("__FRAME_B64__", _TINY_JPEG_B64)
    js = _stand_is_ready_when_child_is_up(js)
    # Ребёнок «браузера» слушает порт и называет свой PID: по нему и видно, убит ли он.
    child_code = (
        "const http=require('http'),fs=require('fs');"
        "const s=http.createServer(()=>{});"
        "s.listen(0,'127.0.0.1',()=>fs.writeFileSync(process.env.FAKE_CHILD_PORT_FILE,"
        "s.address().port+' '+process.pid));")
    assert "'-e', 'setInterval(() => {}, 1000);'" in js, "поддельный браузер изменился"
    js = js.replace("'-e', 'setInterval(() => {}, 1000);'",
                    "'-e', " + json.dumps(child_code))
    stand.write_text(js, encoding="utf-8")
    page = tmp_path / "render.html"
    page.write_text("<html><body>страница рендера</body></html>", encoding="utf-8")
    port_file = tmp_path / "child_port.txt"

    env = dict(os.environ)
    env["REELSI_CAPTURE_CHROME_APP"] = "node"       # вместо chrome.exe — стенд на node
    env["FAKE_CHROME_FLAGS_FILE"] = str(tmp_path / "fake_flags.json")
    env["FAKE_CHILD_PORT_FILE"] = str(port_file)

    cmd = _python_capture_cmd(tmp_path, chrome=str(stand))
    cmd[cmd.index("--url") + 1] = page.as_uri()
    cmd[cmd.index("--frames") + 1] = "2"
    cmd[cmd.index("--profile") + 1] = str(tmp_path / "профиль")
    frames_file = tmp_path / "кадры.bin"
    err_file = tmp_path / "лог.txt"
    with open(frames_file, "wb") as fo, open(err_file, "wb") as fe:
        rc = subprocess.call(cmd, stdout=fo, stderr=fe, env=env, timeout=120)
    err = err_file.read_bytes().decode("utf-8", "replace")

    assert rc == 0, f"съёмщик вышел кодом {rc}:\n{err[-1500:]}"
    assert "#done 2" in err, err
    assert port_file.is_file(), f"поддельный браузер не поднял ребёнка:\n{err[-800:]}"
    port_text, pid_text = port_file.read_text().strip().split()
    port = int(port_text)
    pid = int(pid_text)
    # Кусок доснят нормально — и Chrome погашен ВМЕСТЕ с деревом. Две проверки, потому
    # что у каждой свой способ соврать:
    #   PID — «процесса больше нет». Точно, но номер PID система переиспользует, и на
    #   занятый чужим процессом номер тест упал бы на ровном месте;
    #   порт — «ребёнок больше не слушает». Проверка через сокет видит и чужого
    #   слушателя, зато номер, занятый ребёнком НАШЕГО стенда, — это ровно то, что
    #   должно замолчать.
    # Вместе они дают и «порт ребёнка освобождён», и «нашего процесса нет»; порознь
    # каждая — неполная (раньше была только портовая, и она падала, когда номер порта
    # после смерти ребёнка успевал достаться другому процессу).
    assert not _pid_alive(pid), (
        f"ребёнок Chrome (PID {pid}) остался жить: дерево не погашено "
        "(так и оставалось 155 процессов)")
    assert not _port_alive(port), (
        f"ребёнок Chrome (PID {pid}) остался слушать порт {port}: дерево не погашено "
        "(так и оставалось 155 процессов)")


# --------------------------------------------------------------------------- #
# 6. Камера вне куска не роняет кусок на перемотку, а пропажа называет адрес
# --------------------------------------------------------------------------- #
_AWAY_JS = r"""
(async function(){
  var errs=[];
  var realCreate=document.createElement,realErr=console.error;
  fetch=function(u,o){return Promise.resolve({status:404});};
  document.createElement=function(tag){var el=realCreate(tag);
    if(tag==='img'){el.naturalWidth=1080;el.naturalHeight=1920;
      el.decode=function(){
        // Картинок камеры 1 (в куске её нет) не будет; камера 0 — на месте.
        if(String(el.src).indexOf('c1_')>=0)return Promise.reject(new Error('нет картинки'));
        return Promise.resolve();};}
    return el;};
  console.error=function(m){errs.push(String(m));};
  // Кусок — [180, 60) при 60 к/с, то есть 3.0..4.0 с. Камера 1 показывается ДО него.
  IPV.segs=[{ts:0,te:3.0,src:100,ci:1},{ts:3.0,te:6.0,src:0,ci:0}];
  IPV_FRAMES='C:/clip/_tmp/webrender_1_2/frames_0';IPV_FEXT='jpg';IPV_FRANGE=[180,60];
  function shot(){return {seeks:SEEKS(),src:CAMSRC(),
    fr:IPV_FRAME?[IPV_FRAME.ci,IPV_FRAME.f]:null,dead:Array.from(IPV_FDEAD),
    miss1:IPV_FMISSN.get(1)||0,miss0:IPV_FMISSN.get(0)||0,
    imgs:IPV_FIMGS.size,msgs:errs.slice()};}
  // 1) Три картинки камеры 1 подряд не пришли — ровно то, что было на живом прогоне.
  for(var i=0;i<IPV_FMISS_MAX;i++)await ipvFrameReady(1,100+i);
  await new Promise(function(r){setTimeout(r,5);});
  var away=shot();
  // 2) Кадры ВНУТРИ куска: камера 0, её картинки на месте — кусок идёт картинками.
  await ipvRenderAt(3.0);
  var inside1=shot();
  await ipvRenderAt(3.0+1/PLAN.fps);
  var inside=shot();
  console.error=realErr;
  console.log(JSON.stringify({away:away,inside1:inside1,inside:inside}));
})().catch(function(e){console.error(e&&e.stack||String(e));process.exit(1);});
"""


@node
def test_camera_outside_the_chunk_does_not_drop_it_to_seek():
    """Пропажа картинок у камеры, которой в куске НЕТ, кусок на перемотку не уводит.

    Живой прогон: страница просила кадры камеры, которой на этом куске в монтаже нет
    (404), пропажи считались ЕЙ, после трёх подряд камера объявлялась «без картинок» — и
    кусок уходил на перемотку `<video>` (149 кадров по 94 мс вместо 22). Теперь пропажи
    считаются только у камер, которые в куске показываются, а в лог уходит ПЕРВЫЙ
    отсутствующий адрес — по нему и видно, какую картинку и в какой папке просили.
    """
    out = _run_node(_stand(extra=_AWAY_JS))

    # 1) Камера вне куска пропажей не считается и «мёртвой» не помечается.
    assert out["away"]["miss1"] == 0, out["away"]
    assert out["away"]["dead"] == [], out["away"]
    # 2) Про пропажу всё равно сказано — и назван АДРЕС картинки, которой нет.
    assert out["away"]["msgs"], out
    assert "frames_0%2Fc1_100.jpg" in out["away"]["msgs"][0], out["away"]["msgs"]
    assert "404" in out["away"]["msgs"][0], out["away"]["msgs"]
    assert "не показывается" in out["away"]["msgs"][0], out["away"]["msgs"]
    # 3) Кусок продолжает рисоваться картинками: перемотки нет вовсе.
    assert out["inside1"]["src"] == "img", out["inside1"]
    assert out["inside1"]["fr"] == [0, 0], out["inside1"]
    assert out["inside"]["src"] == "img" and out["inside"]["fr"] == [0, 1], out["inside"]
    assert out["inside"]["seeks"] == [0], out["inside"]
    assert out["inside"]["miss0"] == 0, out["inside"]


# --------------------------------------------------------------------------- #
# 7. Правило «время → кадр» на границах куска
# --------------------------------------------------------------------------- #
# EDL, у которого смены камеры стоят РОВНО на границах кусков рендера: куски при
# 600 кадрах и четырёх экземплярах — по 150 кадров, то есть 2.5 / 5.0 / 7.5 с. Именно на
# этих временах расходятся выбор куска EDL и округление исходного кадра — а расхождение
# на кадр это ДРУГАЯ картинка на странице и 404 у выемки (файла для чужой камеры в папке
# куска нет).
_BOUNDARY_SEGS = [
    {"ci": 0, "ts": 0.0, "te": 2.5, "src": 0.0},
    {"ci": 1, "ts": 2.5, "te": 5.0, "src": 100.25},
    {"ci": 0, "ts": 5.0, "te": 7.5, "src": 12.0},
    {"ci": 1, "ts": 7.5, "te": 10.0, "src": 200.5},
]
_BOUNDARY_FPS = 60
_BOUNDARY_TOTAL = 600

_BOUNDARY_JS = r"""
var IPV={segs:%(segs)s,fps:%(fps)s};
%(funcs)s
var TIMES=%(times)s;
console.log(JSON.stringify(TIMES.map(function(tm){
  var r=ipvSrcFrameAt(tm);return r?[r.ci,r.f]:null;})));
"""


@node
def test_frame_rule_agrees_on_chunk_boundaries():
    """Исходный кадр по `src_frame_at` (Python) и по `ipvSrcFrameAt` (JS) — один и тот же.

    Сверяются ГРАНИЦЫ кусков рендера и их соседи, плюс ровная половина кадра на каждой
    из этих границ: на половине кадра расходятся `round` (Python) и `Math.round` (JS), а
    на самой границе — выбор куска EDL и исходное время камеры. Промах на кадр здесь
    стоит не «одного кадра»: страница просит картинку, которой у выемки нет.
    """
    chunks = wr._frame_chunks(0, _BOUNDARY_TOTAL, 4)
    times: set[float] = set()
    for first, count in chunks:
        for f in (first - 1, first, first + 1,
                  first + count - 1, first + count, first + count + 1):
            if f < 0:
                continue
            times.add(f / _BOUNDARY_FPS)
            times.add((f + 0.5) / _BOUNDARY_FPS)      # ровная половина кадра
    ordered = sorted(times)

    funcs = "\n".join([_func(_read(JS85), n) for n in ("ipvSrcFrameAt", "ipvCamTimeAt")]
                      + [_func(_read(JS60), "pvSegAt")])
    page = _run_node(_BOUNDARY_JS % {"segs": json.dumps(_BOUNDARY_SEGS), "funcs": funcs,
                                     "fps": _BOUNDARY_FPS, "times": json.dumps(ordered)})
    extract = [[r[0], r[1]] if r else None
               for r in (wr.src_frame_at(_BOUNDARY_SEGS, _BOUNDARY_FPS, t) for t in ordered)]

    assert page == extract, "\n".join(
        f"{t}: страница {a}, выемка {b}"
        for t, a, b in zip(ordered, page, extract) if a != b)
    # Проверка не пустая: обе камеры в ряду есть, и склейка приходится на границу куска.
    assert {None if not r else r[0] for r in page} == {0, 1}, page[:5]
    edge = ordered.index(2.5)               # первый кадр второго куска и смена камеры
    assert page[edge] == extract[edge] and page[edge][0] == 1, page[edge]
    assert len({r[1] for r in page if r}) > 5, page[:5]
