# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Куски кадров и экземпляры съёмщика: скорость рендера без After Effects.

Кадры ролика делятся на куски, и каждый кусок снимает СВОЙ экземпляр Chrome (у каждого
свой профиль: одному браузеру профиль занят). Проверяется здесь ровно то, на чём это
может развалиться молча — молча, потому что ролик либо соберётся не тот, либо не за то
время:

1. **Куски покрывают ролик целиком и по разу.** Сумма кусков равна числу кадров,
   куски идут подряд без дыр и наложений: пропущенный или задвоенный кадр — это сдвиг
   всего ролика после него, а не «одного кадра нет».
2. **Число экземпляров — настройка.** По умолчанию половина ядер, но не больше 4;
   кусков не больше, чем кадров, и не больше, чем просили.
3. **Куски стартуют РАЗОМ, а не по очереди.** Живой прогон: 300 кадров, 4 экземпляра,
   ~0.23 с на кадр — итог 59 с, ровно как у ОДНОГО экземпляра: второй Chrome стартовал
   только на кадре 210, потому что цикл запускал кусок и тут же ждал его конца.
   Проверяется дверью «стартовали все, и только потом пошли кадры» и НАЛОЖЕНИЕМ
   интервалов съёмки: кусок, начавший позже, чем кончил сосед, — это очередь.
   Мутация (последовательный цикл) обязана валить ту же проверку — она собирается из
   испорченного исходника. По секундам «разом» не проверяется: под нагрузкой
   многопроцессного прогона `time.sleep` уезжает вместе с очередью потоков, и порог
   «меньше 0.7 от суммы» падал на занятой машине сам по себе.
4. **Каждый кусок кодирует СВОЙ сегмент, склейка — без перекодирования.** Сегменты
   собираются `ffmpeg -f concat -safe 0 -i список -c copy`: кодек и параметры у всех
   одни и те же, поэтому лишний ключевой кадр на стыке не виден, а держать кадры в
   памяти ради одного потока нельзя — на трёхминутном ролике это гигабайты.
5. **Стык без дубля и пропуска.** Кадры каждого куска уходят в СВОЙ сегмент ровно по
   разу, а список склейки перечисляет сегменты в порядке ролика.
6. **Ошибка и отмена гасят ВСЕ процессы, временные сегменты убираются.** Брошенный
   Chrome остался бы висеть со своим профилем, а сегменты — это гигабайты на диске.
7. **Прогресс считает кадры ролика, а не куска** — иначе полоса прыгала бы назад на
   каждом новом экземпляре.

Запуск: python -m pytest tests/test_webrender_instances.py -q
"""
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import core.webrender as wr  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. Нарезка кадров на куски
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("first,count,instances", [
    (0, 100, 1), (0, 100, 2), (0, 100, 3), (0, 100, 4),
    (0, 10, 3), (0, 3, 4), (0, 1, 4), (50, 90, 3), (7, 5, 2),
])
def test_frame_chunks_cover_the_clip_exactly_once(first, count, instances):
    """Куски идут подряд, покрывают ровно `count` кадров и не налезают друг на друга."""
    chunks = wr._frame_chunks(first, count, instances)
    assert chunks, chunks
    assert len(chunks) <= max(1, min(instances, count)), chunks
    pos = first
    for cfirst, ccount in chunks:
        assert cfirst == pos, ("куски не подряд", chunks)
        assert ccount > 0, chunks
        pos += ccount
    assert pos == first + count, ("покрыт не весь ролик", chunks)
    # Разница между кусками — не больше кадра: нарезка равными долями, кадры целые.
    sizes = [c for _f, c in chunks]
    assert max(sizes) - min(sizes) <= 1, sizes


def test_frame_chunks_never_give_more_chunks_than_frames():
    """Кусков не больше, чем кадров: пустой кусок — это лишний Chrome ни за что."""
    chunks = wr._frame_chunks(0, 2, 8)
    assert len(chunks) == 2 and all(c > 0 for _f, c in chunks), chunks


def test_default_instances_is_half_the_cores_but_not_more_than_four(monkeypatch):
    """По умолчанию — половина ядер, но не больше 4 (память: у каждого свой Chrome)."""
    for cpus, want in ((1, 1), (2, 1), (4, 2), (8, 4), (16, 4), (32, 4)):
        monkeypatch.setattr(wr.os, "cpu_count", lambda c=cpus: c)
        assert wr._default_instances() == want, (cpus, wr._default_instances())
    monkeypatch.setattr(wr.os, "cpu_count", lambda: None)
    assert wr._default_instances() == 1, "без числа ядер экземпляр должен остаться один"


def test_instances_is_a_setting_from_the_command_line():
    """Число экземпляров — настройка: параметр рендера и ключ CLI."""
    args = wr._parse(["clip.xml", "--out", "out.mp4", "--instances", "2"])
    assert args.instances == 2, args
    assert wr._parse(["clip.xml", "--out", "out.mp4"]).instances == 0, "0 — «по умолчанию»"


# --------------------------------------------------------------------------- #
# 2. Стенд: заглушки процессов, дверь «все запущены», задержка на кадр
# --------------------------------------------------------------------------- #
# Числа стенда. 30 к/с и 0.6 с — 18 кадров на три куска: по 6 кадров и по 0.05 с
# задержки на кадр. Разом — 0.3 с, по очереди — 0.9 с: разницу видно числом, а не на глаз.
FPS = 30
DUR = 0.6
INSTANCES = 3
FRAMES = int(round(FPS * DUR))          # 18
PER_FRAME = 0.05                        # задержка «съёмки» одного кадра
MAX_S = FRAMES / INSTANCES * PER_FRAME  # 0.3 с — время самого долгого куска
SUM_S = FRAMES * PER_FRAME              # 0.9 с — время последовательного цикла


class _Rec:
    """Приёмник кадров вместо stdin кодировщика: копит байты и «закрывается» как труба."""

    def __init__(self):
        self.buf = b""
        self.closed = False

    def write(self, b):
        self.buf += b

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _Gate:
    """Дверь «все экземпляры запущены»: открывается, когда стартовал последний.

    Так проверяется главное: цикл обязан ЗАПУСТИТЬ все куски, а снимать их будет
    каждый свой поток. Последовательный цикл (мутация) встаёт на этой двери: первый
    кусок уже снимает, а второй ещё не запущен.
    """

    def __init__(self, n):
        self.n = n
        self.opened = threading.Event()
        self._started = 0
        self._lock = threading.Lock()

    def starting(self):
        with self._lock:
            self._started += 1
            if self._started >= self.n:
                self.opened.set()

    def wait(self, timeout):
        return self.opened.wait(timeout)


class _Stream:
    """Поток кадров съёмщика: `[длина][кадр]` с задержкой на кадр и общей дверью.

    Задержка — «кусок снимается столько-то»: по ней видно, идут экземпляры разом
    (куски работают одновременно) или по очереди (второй начинается после первого).

    Времена кадров пишутся в `order`, если он дан: `_StartOrder` по ним отвечает,
    снимали ли куски РАЗОМ. По секундам этого не проверить: под нагрузкой прогона
    `time.sleep` уезжает вместе с очередью потоков, и «общее время меньше суммы»
    перестаёт быть признаком — оба числа растут от нагрузки, а не от устройства кода.

    ПОСЛЕДНИЙ кадр куска придерживается, пока каждый кусок не снял хотя бы один
    (`_hold_last_frame`): из этого и следует, что «кусок ещё не кончил» — свойство
    стенда, а не расписания потоков на медленной машине.
    """

    def __init__(self, frames, delay=0.0, gate=None, order=None, chunk=None):
        self._frames = list(frames)
        self._buf = b""
        self._delay = delay
        self._gate = gate
        self._order = order
        # Номер СВОЕГО куска известен уже здесь: куски стартуют разом, и к моменту
        # первого кадра общий счётчик кассы уже указывает на последнего запущенного —
        # записывать время по нему значило бы приписать все кадры одному куску.
        self._chunk = 0 if chunk is None else int(chunk)
        self._started = False
        self._ended = False                 # кадры кончились: кусок кончил читать

    def _hold_last_frame(self):
        """Придержать последний кадр куска, пока КАЖДЫЙ кусок не снял хотя бы один.

        Без этого быстрый кусок доснял бы свои кадры до того, как медленный снял первый,
        и «ни один ещё не кончил» стало бы неправдой не от кода, а от скорости машины:
        на медленном раннере интервалы съёмки перестают накладываться. Дверь открывает
        `_StartOrder` по кадру последнего куска, а не часы.
        """
        if self._order is None or not self._order.n:
            return
        # Дверь не открылась (своих кусков меньше, чем ждём) — кадр всё же отдаём: тест
        # обязан упасть на «отмена не пришла», а не встать здесь насмерть.
        self._order.wait_all_shot(15.0)

    def read(self, n):
        if not self._started:
            self._started = True
            if self._gate is not None and not self._gate.wait(5.0):
                # Кадры не отдаём: кусок начал снимать раньше, чем запустились остальные.
                raise AssertionError(
                    "кусок начал снимать раньше, чем запустились все экземпляры")
        if not self._buf:
            if not self._frames:
                if not self._ended:
                    self._ended = True
                    if self._order is not None:
                        self._order.finished(self._chunk)
                return b""
            if len(self._frames) == 1:
                self._hold_last_frame()
            frame = self._frames.pop(0)
            if self._delay:
                time.sleep(self._delay)
            self._buf = struct.pack(">I", len(frame)) + frame
            if self._order is not None:
                self._order.read(self._chunk)
                if not self._frames:
                    # Последний кадр куска отдан — этим кусок и кончил снимать. Отмечаем
                    # ЗДЕСЬ, а не на пустом чтении: поток кадров читает ровно свои кадры и
                    # за конец не заходит, поэтому `finished` с EOF не придёт вовсе.
                    self._order.finished(self._chunk)
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk


class _StartOrder:
    """Когда какой кусок начал и кончил снимать и когда съёмку прервали.

    Время читается тем же `time.monotonic`, что у рендера, а не «порядком вызовов»:
    признак «разом» — интервалы кусков НАКЛАДЫВАЮТСЯ. Последовательный цикл (мутация в
    `_mutated_module`) даёт интервалы, которые только стыкуются: следующий кусок
    начинается ПОСЛЕ конца предыдущего.

    Здесь же — ДВЕРЬ ОТМЕНЫ (`shooting`): «каждый кусок снял хотя бы один кадр, и ни один
    ещё не кончил». По секундам этого не сказать: на медленном раннере цикл запуска кусков
    идёт медленнее часов, и «прошло 0.18 с» наступало, когда первые куски успели доснять,
    а последний не снял ни одного кадра, — интервалы уже не накладывались, и тест падал на
    скорости машины, а не на коде. Дверь защёлкивается в тот самый миг, когда последний
    кусок снял ПЕРВЫЙ кадр (`instances` — сколько кусков ждать).
    """

    def __init__(self, instances: int = 0):
        self.n = int(instances)
        self.first: dict[int, float] = {}
        self.last: dict[int, float] = {}
        self.done: set[int] = set()          # куски, доснявшие все свои кадры
        self.stopped: float | None = None    # миг «Стоп»: брошенные куски кончили здесь
        self._all_shot = threading.Event()   # «каждый кусок снял хотя бы кадр»
        self._shooting = False               # защёлка двери отмены
        self._lock = threading.Lock()

    def read(self, chunk):
        """Кадр отдан куску `chunk`: его первый кадр и последний (время съёмки)."""
        now = time.monotonic()
        with self._lock:
            self.first.setdefault(chunk, now)
            self.last[chunk] = now
            if self.n and len(self.first) >= self.n:
                # Защёлка под ТЕМ ЖЕ замком, что и запись кадра: иначе кусок, отдавший
                # последний кадр, успел бы отметиться «кончил» между проверкой и защёлкой,
                # и отмена не нашла бы уже ни одного живого куска.
                self._shooting = self._shooting or not self.done
                self._all_shot.set()

    def finished(self, chunk):
        """Кусок доснял свои кадры: его интервал кончился на последнем кадре."""
        with self._lock:
            self.done.add(chunk)
            self.last.setdefault(chunk, time.monotonic())

    def wait_all_shot(self, timeout):
        """Ждать, пока каждый кусок снял хотя бы кадр: на этом стоит задержка последнего."""
        return self._all_shot.wait(timeout)

    def shooting(self) -> bool:
        """Съёмка идёт РАЗОМ: каждый кусок снял кадр, и ни один ещё не кончил.

        Защёлка, а не «сейчас `not self.done`»: кусок, придержанный на последнем кадре,
        отдаёт его сразу после двери, и проверка «кончил ли» в тот же миг увидела бы его
        конец — отмена не пришла бы вовсе.
        """
        return self._shooting

    def stop(self):
        """«Стоп»: брошенные куски кончили снимать ЗДЕСЬ, а не на своём последнем кадре.

        Без этой отметки кусок, пойманный отменой между кадрами, выглядел бы кончившим
        раньше соседа, и наложение интервалов зависело бы от расписания потоков.
        """
        now = time.monotonic()
        with self._lock:
            if self.stopped is None:
                self.stopped = now
            for k in self.first:
                if k not in self.done:
                    self.last[k] = max(self.last.get(k, now), now)

    def overlapped(self) -> bool:
        """Снимали ли куски РАЗОМ: у каждого начало раньше, чем кончил другой.

        Один кусок (или ни одного) — накладываться нечему, и это не нарушение.
        """
        chunks = sorted(set(self.first) | set(self.last))
        if len(chunks) < 2:
            return True
        ends = [self.last.get(k, 0.0) for k in chunks]
        return all(self.first.get(k, 0.0) < max(ends[j] for j in range(len(chunks)) if j != i)
                   for i, k in enumerate(chunks))


def _new_state(**kw):
    """Копилка стенда: команды процессов, кадры в трубах, погашенные PID и строки лога."""
    state = {"cap": [], "enc": [], "concat": [], "concat_list": "", "enc_in": [],
             "killed_pids": [], "killed_procs": [], "lines": []}
    state.update(kw)
    return state


def _fake_popen(state, delay=0.0, gate=None, order=None):
    """Съёмщик, кодировщик куска и склейка вместо настоящих процессов.

    Кадры куска — по его `--start`/`--frames`: по ним видно, что каждый кусок ушёл в
    СВОЙ сегмент, а список склейки собрал сегменты в порядке ролика. `state["alive"]` —
    процессы «ещё живы» (`poll` возвращает None): так проверяется, что уборка гасит их
    по PID. Список склейки читает ЗАГЛУШКА склейки — как настоящий ffmpeg: рендер
    убирает его за собой, и после рендера в папке его уже нет.
    """

    class FakeFF:
        """ffmpeg: и кодировщик куска (`-f image2pipe`), и склейка (`-f concat`)."""

        def __init__(self, cmd, **k):
            self.cmd = list(cmd)
            self.concat = cmd[cmd.index("-f") + 1] == "concat"
            self.stdin = None if self.concat else _Rec()
            self.stderr = io.BytesIO(b"")
            if self.concat:
                state["concat"].append(self.cmd)
                with open(cmd[cmd.index("-i") + 1], encoding="utf-8") as f:
                    state["concat_list"] = f.read()
            else:
                state["enc"].append(self.cmd)
                state["enc_in"].append(self.stdin)

        def poll(self):
            return None if state.get("alive") else 0

        def wait(self):
            rc = int(state.get("ff_rc", 0))
            if rc == 0 and not state.get("alive"):
                # «ffmpeg записал свой файл»: сегмент куска или итоговую картинку.
                with open(self.cmd[-1], "wb") as f:
                    f.write(b"mp4")
            return rc

        def kill(self):
            state["killed_procs"].append(self.cmd[-1])

    class FakeCap:
        """Съёмщик кадров: свой профиль, свой кусок кадров, свой PID своего Chrome."""

        def __init__(self, cmd, **k):
            state["cap"].append(list(cmd))
            chunk = int(cmd[cmd.index("--chunk") + 1])
            if gate is not None:
                gate.starting()
            self.cmd = list(cmd)
            self.pid = 111
            first = int(cmd[cmd.index("--start") + 1])
            count = int(cmd[cmd.index("--frames") + 1])
            # Кадр узнаваем по номеру: по нему видно, что куски склеены ПО ПОРЯДКУ.
            frames = [b"F%05d" % (first + i) for i in range(count)]
            self.stdout = _Stream(frames, delay, gate, order, chunk=chunk)
            self.stderr = io.BytesIO(b"#chrome-pid 4242\n")

        def poll(self):
            return None if state.get("alive") else 0

        def wait(self):
            return int(state.get("cap_rc", 0))

        def kill(self):
            state["killed_procs"].append("cap")

    def fake(cmd, **k):
        if cmd and cmd[0] == "ffmpeg":
            return FakeFF(cmd, **k)
        return FakeCap(cmd, **k)

    return fake


def _render_env(mod, state, tmp_path, monkeypatch, **fake_kw):
    """Обвязка рендера на заглушках: план, прокси, тело сборки, процессы, гашение.

    `mod` — модуль рендера: настоящий или собранный из ИСПОРЧЕННОГО исходника (мутация).
    Подменяются ЕГО имена, а процессы — общий `subprocess.Popen`: модуль держит ссылку
    на тот же модуль subprocess, и подмена видна обоим.
    """
    xml = tmp_path / "clip.xml"
    xml.write_text("<x/>", encoding="utf-8")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(mod, "_scene_plan", lambda host, body: {"fps": FPS, "w": 1080,
                                                               "h": 1920, "dur": 10})
    # EDL для выемки кадров из исходников — заглушкой: сервера у теста нет, и без EDL
    # рендер кадры не вынимает (страница перематывает сама), что обвязке и нужно.
    monkeypatch.setattr(mod, "_render_edl", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_save_body", lambda host, body: "abc123")
    monkeypatch.setattr(mod, "_tmp_dir", lambda x: str(tmp_path))
    monkeypatch.setattr(mod, "pick_codec", lambda purpose: types.SimpleNamespace(
        args=["-c:v", "libx265"], label="cpu"))
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(state, **fake_kw))
    # Гашение по PID — заглушкой: тест не должен трогать процессы машины.
    monkeypatch.setattr(mod, "_kill_pid", lambda pid: state["killed_pids"].append(pid))
    monkeypatch.setattr(mod, "kill_tree", lambda p: state["killed_procs"].append("tree"))
    return str(xml), out


def _emit(state):
    return lambda line="", **v: state["lines"].append(line)


def _frames_in_pipe(buf):
    """Кадры, как их видит кодировщик: съёмщик шлёт `[длина][кадр]`, а в трубу уезжает
    САМ кадр (mjpeg-поток) — префикс длины снимает `_pump_frames`. Кадры-заглушки одного
    размера, поэтому нарезка по длине кадра."""
    size = len(b"F%05d" % 0)
    assert len(buf) % size == 0, len(buf)
    return [buf[i:i + size] for i in range(0, len(buf), size)]


def _segs_in_work(tmp_path):
    """Файлы сегментов, оставшиеся в рабочей папке рендера (их быть не должно)."""
    out = []
    for name in os.listdir(tmp_path):
        if name.startswith("webrender_"):
            work = tmp_path / name
            out += [str(work / f) for f in os.listdir(work)]
    return out


def _render(mod, state, xml, out, **kw):
    """Рендер стенда: куски, звука нет (он проверяется своим файлом тестов)."""
    kw.setdefault("dur", DUR)
    kw.setdefault("instances", INSTANCES)
    kw.setdefault("audio", False)
    return mod.render(xml, str(out), emit=_emit(state), **kw)


# --------------------------------------------------------------------------- #
# 3. Куски стартуют разом
# --------------------------------------------------------------------------- #
def test_all_chunks_start_before_any_frames_are_read(tmp_path, monkeypatch):
    """Все экземпляры запускаются ДО того, как первый кусок отдаст кадр (дверь).

    Живой прогон: 300 кадров, 4 экземпляра — 59 с, ровно как у одного: второй Chrome
    стартовал только на кадре 210, потому что цикл ждал конца предыдущего куска. Дверь
    открывает последний запущенный экземпляр, а кадры до этого не отдаются вовсе:
    последовательный цикл на ней встаёт, и рендер не собирается.
    """
    state = _new_state()
    xml, out = _render_env(wr, state, tmp_path, monkeypatch, gate=_Gate(INSTANCES))
    res = _render(wr, state, xml, out)
    assert res["ok"], res
    assert res["frames"] == FRAMES, res
    assert len(state["cap"]) == INSTANCES, state["cap"]


def test_render_time_is_the_slowest_chunk_not_the_sum(tmp_path, monkeypatch):
    """Куски снимаются РАЗОМ: съёмка каждого накладывается по времени на соседей.

    Задержка стоит на каждом кадре куска, и признаки «разом» два: дверь «все
    запущены» (`test_all_chunks_start_before_any_frames_are_read`) и НАЛОЖЕНИЕ
    интервалов съёмки. Второй здесь и проверяется: у каждого куска записано время его
    первого и последнего кадра, и кусок, начавший снимать позже, чем кончил предыдущий, —
    это очередь, а не экземпляры разом.

    Почему НЕ «общее время меньше 0.7 от суммы» (так было): под нагрузкой
    многопроцессного прогона `time.sleep` в потоке стенда уезжает вместе с очередью
    потоков, оба числа растут ВМЕСТЕ с загрузкой машины, а не с устройством кода. Замер
    шести параллельных прогонов: 0.31–0.45 с при пороге 0.63 (максимум 0.30, сумма
    0.90) — то есть проверка стояла в полутора шагах от ложного падения и падала
    «сама», когда машина занята. Наложение интервалов от загрузки не зависит: чтобы его
    сломать, надо вернуть последовательный цикл — что и делает мутация ниже.

    Последний кадр куска стенд придерживает, пока каждый кусок не снял первый
    (`_StartOrder(INSTANCES)`): кусок, доснявший свои кадры раньше, чем медленный сосед
    снял ПЕРВЫЙ, дал бы интервалы, которые не накладываются, — и падение теста зависело
    бы от скорости машины, а не от кода.

    Секунды остались только нижней границей: она доказывает, что задержка стенда
    отработала и мерить есть что. Верхней границы по времени нет — она была бы
    проверкой загрузки машины.
    """
    state = _new_state()
    order = _StartOrder(INSTANCES)
    xml, out = _render_env(wr, state, tmp_path, monkeypatch, delay=PER_FRAME, order=order)
    t0 = time.monotonic()
    res = _render(wr, state, xml, out)
    elapsed = time.monotonic() - t0
    assert res["ok"], res
    assert elapsed >= MAX_S * 0.5, (
        f"рендер прошёл за {elapsed:.2f} с — задержка стенда не отработала, мерить нечего")
    # Наложение: каждый кусок начал снимать РАНЬШЕ, чем кончил последний из соседей.
    # Последовательный цикл даёт интервалы, которые только стыкуются: следующий кусок
    # начинается после конца предыдущего, и здесь это видно без секунд.
    assert order.overlapped(), (
        "куски снимались ПО ОЧЕРЕДИ, а не разом: интервалы съёмки не накладываются — "
        f"начало/конец по кускам {sorted(order.first.items())} / "
        f"{sorted(order.last.items())}")


def _mutated_module(tmp_path):
    """Модуль рендера из ИСПОРЧЕННОГО исходника: кусок дожидается конца до старта следующего.

    Проверка не «на словах»: тот же исходник с возвращённым последовательным циклом
    (ровно то, что было до правки) собирается отдельным модулем, и тест требует, чтобы
    проверка наложения интервалов из него вышла красной. Так сторож не сможет молча
    перестать ловить очередь экземпляров.
    """
    path_src = os.path.join(ROOT, "core", "webrender.py")
    with open(path_src, encoding="utf-8") as f:
        src = f.read()
    marker = "            jobs.append(_start_chunk(ctx, k, cfirst, ccount, segs[k]))"
    assert marker in src, "старт кусков изменился — мутация устарела"
    broken = src.replace(
        marker,
        "            job = _start_chunk(ctx, k, cfirst, ccount, segs[k])\n"
        "            if job.pump is not None:\n"
        "                job.pump.join()      # ПОСЛЕДОВАТЕЛЬНО: кусок снимается целиком\n"
        "            jobs.append(job)")
    path = tmp_path / "webrender_sequential.py"
    path.write_text(broken, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("webrender_sequential", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # В sys.modules — до исполнения: `@dataclass` ищет там свой модуль (аннотации в нём
    # отложенные, `from __future__ import annotations`), и без записи падает на импорте.
    sys.modules["webrender_sequential"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_mutation_sequential_chunks_are_caught(tmp_path, monkeypatch):
    """Мутация: вернуть последовательный цикл — проверка наложения обязана покраснеть."""
    mod = _mutated_module(tmp_path)
    state = _new_state()
    order = _StartOrder()
    xml, out = _render_env(mod, state, tmp_path, monkeypatch, delay=PER_FRAME, order=order)
    t0 = time.monotonic()
    res = _render(mod, state, xml, out)
    elapsed = time.monotonic() - t0
    assert res["ok"], res
    assert not order.overlapped(), (
        "мутация не поймана: с последовательным циклом интервалы съёмки всё равно "
        f"наложились (начало/конец по кускам {sorted(order.first.items())} / "
        f"{sorted(order.last.items())})")
    # Та же мутация обязана упереться и в секунды: последовательный цикл снимает куски
    # один за другим, а это сумма, а не максимум.
    assert elapsed >= SUM_S * 0.7, (
        "мутация не поймана: с последовательным циклом рендер собрался за "
        f"{elapsed:.2f} с (сумма кусков ≈ {SUM_S:.2f} с, максимум ≈ {MAX_S:.2f} с)")


# --------------------------------------------------------------------------- #
# 4. Сегменты, склейка и уборка
# --------------------------------------------------------------------------- #
def test_each_chunk_encodes_its_own_segment(tmp_path, monkeypatch):
    """Каждый кусок — свой Chrome, свой профиль, свой сегмент и свои кадры ровно по разу."""
    state = _new_state()
    xml, out = _render_env(wr, state, tmp_path, monkeypatch)
    res = _render(wr, state, xml, out)
    assert res["ok"] and res["frames"] == FRAMES, res
    caps, encs = state["cap"], state["enc"]
    assert len(caps) == INSTANCES, [c[c.index("--start"):] for c in caps]
    assert len(encs) == INSTANCES, "на кусок должен быть СВОЙ кодировщик сегмента"
    # Куски: кадры идут подряд и без перехлёста — стык без дубля и пропуска кадра.
    starts = [int(c[c.index("--start") + 1]) for c in caps]
    counts = [int(c[c.index("--frames") + 1]) for c in caps]
    assert sum(counts) == FRAMES, counts
    pos = 0
    for first, count in sorted(zip(starts, counts)):
        assert first == pos, (starts, counts)
        pos += count
    # Профиль у каждого свой (одному браузеру профиль занят), а страница — одна на всех.
    profiles = [c[c.index("--profile") + 1] for c in caps]
    assert len(set(profiles)) == INSTANCES, profiles
    assert len({c[c.index("--url") + 1] for c in caps}) == 1, caps
    # Кадры каждого куска ушли в СВОЙ сегмент и ровно по разу — по номеру кадра.
    segs = [e[-1] for e in encs]
    assert len(set(segs)) == INSTANCES, segs
    for (first, count), pipe in zip(sorted(zip(starts, counts)), state["enc_in"]):
        assert pipe.closed, "труба кодировщика не закрыта — сегмент остался бы недописан"
        assert _frames_in_pipe(pipe.buf) == [b"F%05d" % (first + i) for i in range(count)]


def test_concat_list_glues_the_segments_in_clip_order(tmp_path, monkeypatch):
    """Склейка: список `file '…'` по порядку кусков и `-c copy` без перекодирования."""
    state = _new_state()
    xml, out = _render_env(wr, state, tmp_path, monkeypatch)
    res = _render(wr, state, xml, out)
    assert res["ok"], res
    assert len(state["concat"]) == 1, "склейка должна быть одна, а не по проходу на кусок"
    cmd = state["concat"][0]
    segs = [e[-1] for e in state["enc"]]
    # Команда склейки: concat-демультиплексор, безопасный разбор путей, копия потока.
    assert cmd[:5] == ["ffmpeg", "-y", "-v", "error", "-f"], cmd
    assert cmd[cmd.index("-f") + 1] == "concat", cmd
    assert cmd[cmd.index("-safe") + 1] == "0", cmd
    assert cmd[cmd.index("-c") + 1] == "copy", cmd
    assert "-c:v" not in cmd and "-c:a" not in cmd, f"склейка перекодирует: {cmd}"
    assert cmd[-1] == str(out), cmd
    # Список склейки перечисляет сегменты В ПОРЯДКЕ РОЛИКА — его прочитала заглушка
    # склейки (как настоящий ffmpeg): сам файл рендер за собой убирает.
    assert state["concat_list"] == "".join("file '%s'\n" % s for s in segs), \
        state["concat_list"]


def test_concat_list_escapes_quotes_and_has_no_crlf(tmp_path):
    """Список склейки: кавычка в пути экранируется, перенос строки — один LF.

    `\\r` в конце строки попал бы в имя файла (пути бывают с кириллицей и пробелами),
    а неэкранированная кавычка оборвала бы путь на середине.
    """
    body = wr._concat_list_text(["C:/клип/seg_0.mp4", "C:/it's/seg_1.mp4"])
    assert body == ("file 'C:/клип/seg_0.mp4'\n"
                    "file 'C:/it'\\''s/seg_1.mp4'\n"), body
    assert "\r" not in body, body


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
live_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE),
                                 reason="живая склейка требует ffmpeg и ffprobe в PATH")


@live_ffmpeg
def test_live_concat_gives_the_sum_of_frames_without_a_duplicate_at_the_seam(tmp_path):
    """Живая склейка: кадров и длительности — СУММА сегментов, стык без дубля.

    Заглушки проверяют, что кадры разложены по сегментам и список собран в порядке
    ролика; здесь тот же путь проходит НАСТОЯЩИЙ ffmpeg: три сегмента по 4 кадра при
    30 к/с склеиваются `-c copy`, и в итоге обязано быть ровно 12 кадров и 0.4 с.
    Задвоенный на стыке кадр дал бы 13, пропавший — 11: то и другое видно числом.
    """
    segs = []
    for k in range(3):
        seg = tmp_path / ("seg_%d.mp4" % k)
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
             "testsrc=size=160x120:rate=%d:duration=1" % FPS, "-frames:v", "4",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg)],
            check=True, capture_output=True)
        segs.append(str(seg))
    work = tmp_path / "work"
    work.mkdir()
    list_path = str(work / "concat.txt")
    out = str(work / "out.mp4")
    wr._write_concat_list(list_path, segs)
    subprocess.run(wr._concat_cmd(list_path, out), check=True, capture_output=True)

    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames,duration", "-of", "json", out],
        check=True, capture_output=True, text=True, encoding="utf-8")
    stream = json.loads(probe.stdout)["streams"][0]
    got_frames = int(stream["nb_read_frames"])
    got_dur = float(stream["duration"])
    assert got_frames == 3 * 4, f"кадров в склейке {got_frames}, а сегменты дают {3 * 4}"
    assert abs(got_dur - 3 * 4 / FPS) < 0.02, (
        f"длительность склейки {got_dur} с, а сумма сегментов {3 * 4 / FPS} с")


def test_temp_segments_and_profiles_are_removed_after_the_render(tmp_path, monkeypatch):
    """За рендером не остаётся ни сегментов, ни профилей: это гигабайты на диске."""
    state = _new_state()
    xml, out = _render_env(wr, state, tmp_path, monkeypatch)
    res = _render(wr, state, xml, out)
    assert res["ok"], res
    assert state["enc"], "сегментов не было — проверять нечего"
    left = _segs_in_work(tmp_path)
    assert left == [], f"временные файлы остались: {left}"


def test_failed_chunk_kills_every_process_and_removes_segments(tmp_path, monkeypatch):
    """Ошибка любого куска: гасим ВСЕ процессы по PID, сегменты убираем.

    Кодировщик одного куска падает (нет места, драйвер отказал) — ролик не полный.
    Уборка обязана погасить и съёмщиков, и кодировщиков ВСЕХ кусков: брошенный Chrome
    остался бы висеть со своим профилем и своей страницей.
    """
    state = _new_state(ff_rc=1, alive=True)
    xml, out = _render_env(wr, state, tmp_path, monkeypatch)
    with pytest.raises(Exception) as exc:
        _render(wr, state, xml, out)
    assert "ffmpeg" in str(exc.value), str(exc.value)
    assert sorted(state["killed_procs"]) == ["tree"] * (2 * INSTANCES), state["killed_procs"]
    assert sorted(state["killed_pids"]) == [4242] * INSTANCES, state["killed_pids"]
    assert _segs_in_work(tmp_path) == [], _segs_in_work(tmp_path)


def test_cancel_stops_every_chunk_and_cleans_up(tmp_path, monkeypatch):
    """«Стоп» во время съёмки: куски бросаются, все процессы гасятся, мусор убирается.

    Отмена приходит на середине куска (кадр — 0.05 с, кусок — 0.3 с): все экземпляры
    снимают РАЗОМ, и уборка обязана погасить КАЖДЫЙ — и съёмщика, и его кодировщик:
    брошенный Chrome остался бы висеть со своим профилем и своей страницей.

    Дверь отмены — событие по КАДРАМ, а не часы: «каждый кусок снял хотя бы один кадр,
    и ни один ещё не кончил» (`order.shooting`), — а `cancel` зовёт и цикл запуска
    кусков, и поток кадров (тот спрашивает перед каждым кадром). По секундам это не
    проверяется: на медленном раннере «прошло 0.18 с» наступало, когда первый кусок успел
    доснять, а последний не снял ни одного кадра, — интервалы съёмки не накладывались, и
    тест падал на скорости машины, а не на коде рендера. «Ни один ещё не кончил» держит
    стенд: последний кадр куска придержан, пока каждый кусок не снял первый.
    """
    state = _new_state(alive=True)
    order = _StartOrder(INSTANCES)
    xml, out = _render_env(wr, state, tmp_path, monkeypatch, delay=PER_FRAME, order=order)

    def cancel():
        # Ждать здесь НЕЛЬЗЯ: `cancel` зовётся из ТОГО ЖЕ цикла, что запускает куски
        # (перед каждым), и ожидание последнего экземпляра останавливало запуск
        # предыдущих — ожидание превращалось в клинч на свои же 15 с, и рендер
        # отменялся только после четвёртой попытки. «Ещё не все сняли» здесь — это «не
        # отменяем» (False), а не «подождём»: цикл дойдёт до последнего куска и снова
        # спросит, уже с открытой дверью.
        if not order.shooting():
            return False
        order.stop()      # куски, брошенные отменой, кончили снимать ЗДЕСЬ
        return True

    res = wr.render(xml, str(out), dur=DUR, instances=INSTANCES, audio=False,
                    cancel=cancel, emit=_emit(state))
    assert res["cancelled"] is True and res["ok"] is False, res
    assert len(state["cap"]) == INSTANCES, state["cap"]
    assert 0 < res["frames"] < FRAMES, res
    assert order.overlapped(), (
        "куски снимались по очереди: интервалы съёмки не накладываются — "
        f"начало/конец по кускам {sorted(order.first.items())} / "
        f"{sorted(order.last.items())}")
    # Каждый запущенный кусок погашен — и съёмщик, и его кодировщик.
    assert state["killed_procs"] == ["tree"] * (2 * INSTANCES), state["killed_procs"]
    assert _segs_in_work(tmp_path) == [], _segs_in_work(tmp_path)


# --------------------------------------------------------------------------- #
# 5. Прогресс и настройка экземпляров
# --------------------------------------------------------------------------- #
def test_progress_counts_the_whole_clip_not_the_chunk(tmp_path, monkeypatch):
    """«кадр N/M» считает кадры ВСЕГО ролика: полоса не прыгает назад на новом куске."""
    state = _new_state()
    xml, out = _render_env(wr, state, tmp_path, monkeypatch)
    res = _render(wr, state, xml, out)
    assert res["ok"], res
    lines = [ln for ln in state["lines"] if ln.startswith("кадр ")]
    assert lines, state["lines"][:10]
    assert lines[-1] == "кадр %d/%d" % (FRAMES, FRAMES), lines
    # Каждая строка — про весь ролик (M = 18), а не про кусок (M = 6), и номера не
    # повторяются: куски идут разом, и счётчик у них общий.
    nums = [int(ln.split()[1].split("/")[0]) for ln in lines]
    assert nums == sorted(set(nums)), nums
    assert all(ln.endswith("/%d" % FRAMES) for ln in lines), lines


def test_progress_is_the_sum_of_finished_frames_of_all_chunks():
    """Процент ролика — СУММА готовых кадров кусков, и она не убывает.

    Живой прогон владельца: четыре экземпляра Chrome слали каждый свой «кадр k/N», а в
    строку попадал последний пришедший — процент скакал «15, 16, 15, 16, 15, 17, 18,
    17…», а на отставшем куске уезжал назад («от 50 до 45»). Здесь куски отдают кадры
    ВПЕРЕМЕШКУ (как разом идущие экземпляры) и с разной скоростью, а строки обязаны
    идти строго вверх и кончиться полным роликом.
    """
    seen: list[tuple[str, dict]] = []
    total, each = 120, 30
    progress = wr._Progress(total, lambda line, **v: seen.append((line, v)))
    # Куски идут вразнобой и с разной скоростью: кадры приходят вперемешку, как от
    # разом запущенных экземпляров Chrome. Рядом считаем «номер последнего кадра» —
    # по нему видно, что старая формула (последний пришедший кадр) показывала не то.
    nums: list[int] = []
    last: list[int] = []                   # что попало бы в строку по-старому
    for i in range(each):
        for k, n in ((0, i + 1), (1, i + 1), (2, i + 1), (3, i + 1), (0, i + 2)):
            if n > each:
                continue                   # быстрый кусок успел на кадр больше — он и кончает первым
            nums.append(progress.add(k, n))
            last.append(n)                 # «кадр N/M» по последнему пришедшему
    assert nums == sorted(nums), f"процент поехал назад: {nums}"
    assert nums[-1] == total, nums[-5:]
    assert progress.done == total, progress.done
    # Старая формула на той же последовательности врала бы вдвое: готовых кадров
    # гораздо больше, чем номер последнего пришедшего (три куска уже отсчитали свои).
    assert last[-1] * 2 < total, f"последовательность не воспроизводит отставание: {last[-5:]}"
    # Строки в логе — тот же ряд: монотонно и с полным роликом в конце.
    assert seen, "в лог не ушло ни одной строки прогресса"
    logged = [int(v["n"]) for _line, v in seen]
    assert logged == sorted(set(logged)), logged
    assert logged[-1] == total, logged
    assert all(v["total"] == total for _line, v in seen), seen


def test_progress_counts_each_chunk_only_once_and_never_over_the_total():
    """Один кусок не задваивает ролик, и сумма не перелезает за общее число кадров.

    Кадры куска приходят по порядку, но счётчик не должен зависеть от этого: повтор
    того же числа (перезапрос, повторный вызов) не двигает сумму, а вместе куски
    дают РОВНО число кадров ролика — иначе полоса либо не дойдёт до конца, либо
    покажет больше ста процентов.
    """
    progress = wr._Progress(10, lambda line, **v: None)
    progress.add(0, 4)
    assert progress.done == 4, progress.done
    progress.add(0, 4)                      # тот же кусок отчитался ещё раз
    assert progress.done == 4, "повторный отчёт куска задвоил кадры"
    progress.add(1, 6)
    assert progress.done == 10, progress.done
    progress.add(1, 6)
    progress.add(0, 4)
    assert progress.done == 10, f"кадров больше, чем в ролике: {progress.done}"


class _ProgressWithoutTheGuard(wr._Progress):
    """Мутация: счётчик отдаёт сумму как есть — без «не меньше прежнего».

    Так вёл бы себя счётчик, у которого монотонность держит ПОРЯДОК ВЫЗОВОВ, а не он
    сам: кусок, отставший на кадр, уводит строку назад — и полоса у человека едет назад.
    """

    def add(self, chunk: int, n: int = 1) -> int:
        with self._lock:
            self._per_chunk[chunk] = int(n)
            self._done = sum(self._per_chunk.values())
            return self._done


def test_mutation_progress_that_can_go_backwards_is_caught():
    """Мутация 1: счётчик без сторожа монотонности — проверка обязана покраснеть.

    Проверка не «на словах»: тот же ряд кадров прогоняется через счётчик с возвращённой
    прежней формулой (отдаёт сосчитанное как есть), и тест требует, чтобы ряд поехал
    назад. Так сторож не сможет молча перестать ловить «процент, который скачет».
    """
    # Кадры идут от разом запущенных кусков, и «сколько сейчас» у каждого своё: кусок,
    # начавший считать заново (перезапуск страницы, повторный отчёт), уводит сумму назад.
    ticks = [(0, 1), (0, 2), (0, 3), (0, 4), (1, 1), (1, 2), (1, 3), (1, 1), (1, 2)]
    broken = []
    b = _ProgressWithoutTheGuard(100, lambda line, **v: None)
    for k, n in ticks:
        broken.append(b.add(k, n))
    back = [(a, c) for a, c in zip(broken, broken[1:]) if c < a]
    assert back, f"мутация не поймана: без сторожа ряд остался монотонным — {broken}"

    # Боевой счётчик на тех же числах идёт вверх: «сколько всего» с каждого куска.
    good = wr._Progress(100, lambda line, **v: None)
    nums = [good.add(k, n) for k, n in ticks]
    assert nums == sorted(nums), nums

def test_instances_zero_means_the_default(tmp_path, monkeypatch):
    """`instances=0` — «по умолчанию»: берётся `_default_instances`, а не один экземпляр."""
    state = _new_state()
    xml, out = _render_env(wr, state, tmp_path, monkeypatch)
    monkeypatch.setattr(wr, "_default_instances", lambda: 2)
    res = _render(wr, state, xml, out, instances=0)
    assert res["ok"], res
    assert len(state["cap"]) == 2, state["cap"]


def test_every_instance_is_killed_by_its_own_pid(tmp_path, monkeypatch):
    """Каждый экземпляр гасится по СВОЕМУ PID (все, а не последний).

    Брошенный Chrome остался бы висеть со своей страницей и своим профилем, а по имени
    убивать нельзя: на машине открыт браузер человека.
    """
    state = _new_state()
    xml, out = _render_env(wr, state, tmp_path, monkeypatch)
    res = _render(wr, state, xml, out)
    assert res["ok"], res
    assert state["killed_pids"] == [4242] * INSTANCES, state["killed_pids"]
