# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Окно настроек VST3-плагина — ОТДЕЛЬНЫМ процессом (запускает core/voicefx.py).

Почему отдельный процесс. `show_editor()` из JUCE обязан вызываться в ГЛАВНОМ
потоке процесса: из рабочего потока (а Flask живёт именно в них) окно не
открывается вовсе или роняет процесс целиком — вместе с ним ушёл бы и сервер
интерфейса. Поэтому здесь свой `__main__`: главный поток нового процесса грузит
плагин, ставит ему сохранённое состояние, показывает окно и ждёт закрытия.

    python -m core.voicefx_editor --path X.vst3 [--name Имя] [--state-in in.bin] --state-out out.bin
    python -m core.voicefx_editor --job job.json --state-out out.bin [--live]

`--name` нужен оболочкам (WaveShell): сам файл их не грузит, грузит конкретное имя
внутри. Состояние едет файлом, а не аргументом: raw_state — это байты.

Режим `--job` — звук через цепочку. Без `--live` играет ЗАДАННЫЙ файл задания по
кругу (`frag`): так проверяют и сам процесс, и звук. С `--live` это ЖИВОЙ ХОСТ:
играет ТРЕК (`track`) — голос всего клипа после шумодава и ДО плагинов, — и едет он
вместе с картинкой превью по командам из stdin (JSON-строки, по одной в строке):

    {"cmd": "play",  "at": 12.5}    играть с этой секунды исходника камеры 1
    {"cmd": "seek",  "at": 300.0}   встать на эту секунду (играя или на паузе)
    {"cmd": "pause"}                остановить чтение трека
    {"cmd": "track", "path": ...}   заменить трек (шумодав досчитал весь клип)
    {"cmd": "chain", "chain": […]}  перестроить цепочку НА ЛЕТУ (поток не трогаем)
    {"cmd": "dump_states"}          отдать состояние ВСЕХ загруженных плагинов (событие `states`)
    {"cmd": "open_editor", "index": N, "path": X.vst3}  открыть окно плагина в ЭТОМ же процессе
    {"cmd": "stop"}                 закрыть хост (звук кончился)
    {"cmd": "quit"}                 закрыть хост, не трогая звук

Почему именно stdin, а не файл: сервер передаёт команды ИМЕННО ЭТОМУ процессу, а
файл пришлось бы адресовать (имя сессии, уборка, гонка двух окон). Труба stdin уже
есть у каждого процесса, и конец трубы (EOF) значит «сервер ушёл» — звук тогда
гаснет сам, а не играет в опустевшую комнату.

Живой хост — ОДИН процесс на клип: он и играет трек через цепочку, и открывает окна
плагинов по команде `open_editor`. Так «Настроить» показывает ПАНЕЛЬ уже звучащего
хоста: звук не прерывается, процесс не перезапускается, а правка цепочки (`chain`)
пересобирает доску на лету — поток вывода при этом тот же самый. Окно открывается
ГЛАВНЫМ потоком (JUCE иначе не умеет), а звук идёт своим: команды звука
маршрутизируются отдельно и не ждут, пока человек закроет окно.

Задание `--live` пишет core/voicefx.edit_plugin_live / core/voicefx.live_host:

    {"track": путь к WAV, "track_file": JSON с готовым треком, "start": секунда,
     "chain": [{"path","name","state_b64","on"} …], "index": какой плагин открыть
     ("-1" — не открывать вовсе: хост без окна), "device": имя устройства вывода,
     "events_file": файл-дубль событий для сервера}

Трек грузится ЛЕНИВО: пока `track_file` пуст, звук молчит, а хост уже поднят (и
окно, если его просили, открыто) и человек крутит ручки. Как только шумодав досчитал
— файл появляется, трек подхватывается, и с этого места звук идёт вровень с
картинкой.

В звук идут только включённые плагины (как в `render`), но открыть можно любой,
включая выключенный: его грузим и открываем, а в цепочку не ставим. Плагин, который
не загрузился, НЕ роняет хост: он пропускается, причина с его именем уезжает
родителю событием `chain` (`skipped`), и цепочка работает без него. Звук — вещь
необязательная: нет устройства или он не открылся — хост всё равно жив, а причину
пишем в stderr одной строкой.

События родителю едут JSON-строками в stdout (`_emit_event`): `editor_open`,
`editor_closed`, `state` (состояние плагина по закрытию окна), `states` (состояния
ВСЕХ загруженных плагинов — по команде `dump_states`), `chain` (что реально встало
в цепочку и что пропущено), `empty` (ни одного плагина не встало — играть нечем)
и `audio_error` (звук хоста не идёт: устройство не
приняло частоту, поток отвалился — по нему страница возвращает звук СЕБЕ,
а не глушит свой голос в пользу молчащего хоста). Живой хост дублирует их в
файл событий из задания (`events_file`): его stdout родителю не читает никто,
а про пропущенный плагин и закрытое окно сервер обязан узнать, пока хост ещё жив.

Поток вывода при этом ЗАПУСКАЕТСЯ на всё время игры: созданный, но не запущенный
(`running == False`) он глотает `write` молча, и человек крутит ручки в тишине.
Вход и выход парные — `__enter__` при открытии, `__exit__` в `finally` рабочей
функции, при любом сбое между ними.
"""
from __future__ import annotations
import base64
import json
import os
import queue
import sys
import threading
from typing import Any, Callable, NoReturn, Protocol, Sequence, TextIO, cast

from core import voicefx_proc
from core.umsg import ReelsiError, umsg

# Параметры живого звука. 48 кГц — та частота, в которой трек отдаёт ffmpeg
# (core/voicefx._extract); буфер 512 сэмплов (~11 мс) — обычный размер для
# реального времени. Блок обработки 50 мс: крупнее — и остановка после закрытия
# окна ждёт конца блока, мельче — плагинам с хвостом тяжелее.
SR = 48000
BUFFER = 512
BLOCK = 2400                        # 50 мс при 48 кГц
FADE = 240                          # 5 мс: фейд на стыке трека, чтобы не щёлкало
CHANNELS = 2
# Как часто проверяются команды и замена трека на паузе: ожидание трека идёт
# отдельным шагом, и этот же шаг — время отклика на «Играть» до первого звука.
IDLE_STEP = 0.05
TRACK_POLL = 0.2                    # как часто смотрим, не досчитал ли шумодав трек
# Файл-дубль событий (путь из задания): живой хост живёт часами, а stdout у него
# уходит в `DEVNULL` — по этому файлу сервер и узнаёт про пропущенный плагин, и
# забирает состояние плагина по закрытию окна, не дожидаясь конца хоста.
_EVENTS_FILE = ""


class _Stream(Protocol):
    """Что нам нужно от `pedalboard.io.AudioStream` — и всё (в тестах подменяется).

    `__enter__`/`__exit__` здесь не для красоты: без входа настоящий поток остаётся
    `running == False`, и `write` возвращается, ничего не играя. Выход у pedalboard
    делает stop + close разом (io/AudioStream.h), поэтому при остановке отдельный
    `close()` не нужен — он остаётся только на случай, когда вход не состоялся.
    """

    # Частота, в которой поток РЕАЛЬНО играет: у pedalboard это частота устройства
    # вывода, а не то, что просили при открытии (владелец: просили 48 кГц, устройство
    # 44.1 — поток открылся, а `write` с 48 упал). По ней и берётся ресемплер на выходе.
    sample_rate: float

    def __enter__(self) -> Any: ...

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any: ...

    def write(self, audio: Any, sample_rate: float) -> None: ...

    def close(self) -> None: ...


def _parse_args(argv: Sequence[str]) -> dict[str, str]:
    """Разобрать аргументы в словарь. Своими руками, без argparse.

    Разбор простой, а argparse тянет за собой интерактив и тексты справки —
    ровно то, от чего api/ избавлен (tests/test_api_no_cli.py), и здесь он ни к
    чему: шесть именованных ключей. Ошибки тут — готовая строка без umsg-кода:
    наружу их никто не переводит, они уезжают в stderr процесса и оттуда в
    сообщение voicefx_edit_failed.

    `--live` — флаг, а не пара «ключ значение»: значений у него нет, и разрешать
    ему съесть следующий аргумент нельзя.
    """
    out: dict[str, str] = {"path": "", "name": "", "state-in": "", "state-out": "",
                           "job": "", "live": ""}
    i = 0
    while i < len(argv):
        key = argv[i].lstrip("-")
        if key not in out:
            raise ReelsiError(f"Неизвестный аргумент: {argv[i]}")
        if key == "live":
            out[key] = "1"
            i += 1
            continue
        if i + 1 >= len(argv):
            raise ReelsiError(f"У аргумента {argv[i]} нет значения")
        out[key] = argv[i + 1]
        i += 2
    return out


def _pedalboard() -> Any:
    """Ленивый импорт pedalboard (в тестах подменяется фейком, без нативного кода)."""
    try:
        import pedalboard
    except ImportError:
        raise ReelsiError(umsg("vst_unavailable",
                               "Нет пакета pedalboard — VST-плагины недоступны: "
                               "pip install pedalboard"))
    return pedalboard


def load_plugin(path: str, name: str = "") -> Any:
    """Загрузить VST3 (ленивый импорт pedalboard) — как в core/voicefx.py."""
    return _pedalboard().load_plugin(path, plugin_name=name or None)


def _note(text: str) -> None:
    """Служебная строка в stderr: её хвост уезжает в сообщение voicefx_edit_failed.

    В stderr, а не в stdout: stdout процесса — канал ответов родителю, а причину
    «звука нет» человек должен увидеть в ошибке окна, а не искать в логах.
    """
    print(text, file=sys.stderr, flush=True)


def _audio_error_note(failed: list[str], reason: str) -> None:
    """Сбой звука: причина в stderr И событием в файл событий.

    Событие нужно странице: без него живой хост молчит (устройство 44.1 кГц против
    48, устройство занято, поток отвалился), а превью считает, что звучит хост, и
    глушит свой голос — человек слышит полную тишину и не знает почему.
    """
    failed.append(reason)
    _emit_event("audio_error", reason=str(reason or "звука нет"))
    _note(f"звук прервался ({reason}) — окно открыто без прослушивания")


def _out_rate(stream: Any) -> int:
    """Частота, в которой РЕАЛЬНО играет поток; заглушка без поля — как у трека.

    У настоящего `pedalboard.io.AudioStream` это частота устройства: её же требует
    `write`. Заглушки тестов поля не несут — для них всё как раньше, SR.
    """
    try:
        rate = int(round(float(getattr(stream, "sample_rate", 0) or 0)))
    except (TypeError, ValueError):
        return SR
    return rate if rate > 0 else SR


def _make_resampler(out_rate: int) -> Callable[[Any], Any] | None:
    """Поточный ресемплер SR → частота устройства; None — частоты совпали.

    Ресемплер стоит ПОСЛЕ цепочки: плагины считают на SR, как раньше, и их звук при
    48 кГц не меняется вовсе. Нужен он ровно там, где поток открылся в другой
    частоте: `write` с чужой частотой pedalboard отвергает исключением, и звук
    пропадает целиком (замер владельца: устройство по умолчанию 44.1 кГц).
    """
    if out_rate == SR:
        return None
    pb = _pedalboard()
    try:
        rs = pb.io.StreamResampler(SR, out_rate, CHANNELS)
    except Exception as e:                    # noqa: BLE001 — без ресемплера звук не лучше
        _note(f"ресемплер {SR}→{out_rate} не создался ({type(e).__name__}: {e})")
        return None
    import numpy as np

    def _resample(chunk: Any) -> Any:
        return np.asarray(rs.process(chunk), dtype="float32")

    return _resample


def read_job(path: str) -> dict[str, Any]:
    """Прочитать файл задания. Битый/чужой файл — понятная ошибка, не падение."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ReelsiError(f"Файл задания не прочитан ({path}): {e}")
    if not isinstance(data, dict):
        raise ReelsiError(f"Файл задания не объект: {path}")
    return data


def _chain_items(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Элементы цепочки из задания; битые отбрасываются (как в normalize_fx)."""
    raw = job.get("chain")
    out: list[dict[str, Any]] = []
    for item in (raw if isinstance(raw, list) else []):
        if isinstance(item, dict) and str(item.get("path") or "").strip():
            out.append(item)
    return out


def _emit_event(name: str, /, **vars: Any) -> None:
    """Событие родителю — JSON-строкой в stdout и дублем в файл событий.

    Отдельным каналом от `_note` (stderr): stderr несёт причину сбоя, а stdout —
    то, по чему сервер ведёт сессию (окно открылось/закрылось, состояние, цепочка).
    Печать построчная и с `flush`: иначе сервер ждал бы события до наполнения буфера.

    Файл событий (`_EVENTS_FILE`, путь из задания) — ДУБЛЬ для живого хоста: хост
    живёт часами и stdout родителю не читает никто (он уходит в `DEVNULL`), а узнать
    про пропущенный плагин и про закрытое окно сервер обязан. Дубль — построчный
    JSON; пишем с `flush` сразу: файл читают опросом, и буфер задержал бы событие.
    """
    line = json.dumps({"event": name, **vars}, ensure_ascii=False)
    print(line, flush=True)
    path = _EVENTS_FILE
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass                    # файл событий — удобство родителя, а не условие звука


def _prime(plug: Any) -> None:
    """Подготовить плагин В ГЛАВНОМ ПОТОКЕ: первый прогон тишины с reset=True.

    Настройка частоты, каналов и размера блока — единственное, что pedalboard велит
    делать в главном потоке. После неё рабочий поток гоняет плагин с reset=False.
    Вызывается только из `load_chain`, а тот — только из главного потока хоста.
    """
    import numpy as np
    plug(np.zeros((CHANNELS, BLOCK), dtype="float32"), SR, reset=True)


def load_chain(pb: Any, chain: list[dict[str, Any]],
               keep: dict[tuple[str, str], Any] | None = None
               ) -> tuple[list[tuple[dict[str, Any], Any]], list[dict[str, str]]]:
    """Загрузить плагины цепочки: пары (задание, плагин) по порядку и список пропущенных.

    Состояние из base64 ставим сразу: без него плагин открылся бы с заводскими
    ручками, и человек настраивал бы не то, что стоит в профиле.

    Незагружаемый плагин НЕ роняет хост: он пропускается, причина с его ИМЕНЕМ
    уезжает родителю (событие `chain` → панель «Голос»), и цепочка работает без
    него. Раньше падение `load_plugin` уносило с собой весь процесс окна — «код
    возврата 1» вместо звука.

    `keep` — уже загруженные плагины по (путь, имя). Совпадение берём ГОТОВЫМ, а не
    грузим заново: правка цепочки идёт на лету, с играющим звуком, и повторный
    `load_plugin` — это секунды тишины и потерянный хвост плагина на каждом
    «включил/выключил». Состояние у взятого плагина своё, живое: его правят ручки
    того же окна, и перезаписывать его профилем на каждой правке цепочки значило бы
    сбрасывать только что накрученное.
    """
    out: list[tuple[dict[str, Any], Any]] = []
    skipped: list[dict[str, str]] = []
    for item in chain:
        name = str(item.get("name") or "")
        title = name or os.path.splitext(os.path.basename(str(item["path"])))[0]
        plug = (keep or {}).get((str(item["path"]), name))
        if plug is not None:
            out.append((item, plug))
            continue
        try:
            plug = pb.load_plugin(str(item["path"]), plugin_name=name or None)
        except Exception as exc:  # noqa: BLE001 — чужой плагин не повод ронять хост
            reason = str(exc).strip().splitlines()
            skipped.append({"path": str(item["path"]), "name": title,
                            "reason": reason[-1] if reason else type(exc).__name__})
            _note(f"плагин {title} не загрузился — пропущен")
            continue
        state = item.get("state_b64")
        if isinstance(state, str) and state:
            raw = base64.b64decode(state)
            if raw:
                plug.raw_state = raw
        try:
            _prime(plug)
        except Exception as exc:  # noqa: BLE001 — плагин, не принявший подготовку, пропускаем
            reason = str(exc).strip().splitlines()
            skipped.append({"path": str(item["path"]), "name": title,
                            "reason": reason[-1] if reason else type(exc).__name__})
            _note(f"плагин {title} не подготовился — пропущен")
            continue
        out.append((item, plug))
    return out, skipped


def open_target(loaded: list[tuple[dict[str, Any], Any]], index: int,
                path: str = "") -> Any:
    """Плагин, окно которого открывать: по пути плагина, иначе по индексу.

    Индекс на ВЫКЛЮЧЕННЫЙ плагин — обычное дело (его и правят, чтобы включить):
    такой плагин уже загружен, в звук он не поставлен, но окно открывается. Индекс
    за пределами цепочки — ошибка задания: открывать нечего.

    Индекс считается по ВСЕЙ цепочке, а не по «включённым»: иначе у человека с
    выключенным плагином в середине открывалось бы окно СОСЕДА.

    Путь важнее индекса, и вот почему: плагин, который не загрузился, из списка
    загруженных ВЫПАДАЕТ, и индексы панели (там цепочка целиком) разъезжаются с
    индексами хоста — «Настроить» у соседней строки открыло бы окно не того плагина.
    Путь же один на строку панели и на задание.
    """
    if path:
        for item, plug in loaded:
            if str(item.get("path") or "") == path:
                return plug
        # Путь назван, а в загруженных его нет — плагин ПРОПУЩЕН при загрузке. К индексу
        # тут не возвращаемся: у него уже другой смысл (индексы разъехались), и «Настроить»
        # у пропущенной строки открыло бы окно ЧУЖОГО плагина.
        raise ReelsiError(f"Плагин {path} не загружен — открывать нечего")
    if 0 <= index < len(loaded):
        return loaded[index][1]
    raise ReelsiError(f"В задании нет плагина с индексом {index} — открывать нечего")


def read_fragment(path: str) -> Any:
    """Фрагмент в память: float32, ровно 2 канала, форма (каналы, сэмплы).

    В память целиком, а не потоково: голос клипа длиной в час — это гигабайты, но
    фрагмент прослушивания мал (~8 МБ на 20 с), а игре нужен материал для чтения с
    любого места. Каналы приводим к 2: у голоса камеры 1 бывает и моно, а потоку
    нужен тот же счёт каналов. Форма — (каналы, сэмплы), как её отдаёт
    `AudioFile.read`: режем по времени, а не по отдельным сэмплам.
    """
    pb = _pedalboard()
    import numpy as np
    with pb.io.AudioFile(path) as f:
        audio = f.read(f.frames)
    data = np.asarray(audio, dtype="float32")
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.shape[0] == 1:
        data = np.repeat(data, CHANNELS, axis=0)      # моно -> стерео
    elif data.shape[0] > CHANNELS:
        data = data[:CHANNELS]
    return data


def _fade(data: Any, size: int, up: bool) -> None:
    """Линейный фейд на месте: 5 мс на стыке трека убирают щелчок.

    Концы куска — случайные точки волны, и жёсткая склейка «конец → начало» даёт
    ступеньку: на слух это щелчок. Фейд — по времени, то есть по последней оси:
    блок приходит формой (каналы, сэмплы).
    """
    import numpy as np
    n = min(size, data.shape[-1])
    if n <= 0:
        return
    ramp = np.linspace(0.0, 1.0, n, dtype="float32")
    data[..., :n] *= ramp if up else ramp[::-1]


# --------------------------------------------------------------------------- #
# Живой режим: трек клипа, команды из stdin, звук вровень с картинкой
# --------------------------------------------------------------------------- #
class PlayState:
    """Где стоит трек и играет ли он: одно состояние на все команды.

    Позиция — в СЭМПЛАХ трека, а не в секундах: блок берётся срезом, и пересчёт
    «секунды → сэмплы» в каждом кадре накапливал бы округление. `at` от родителя
    приходит секундами исходника камеры 1, и переводится один раз — в команде.
    """

    def __init__(self, total: int) -> None:
        self.total = max(0, int(total))
        self.pos = 0                # сэмпл, с которого пойдёт следующий блок
        self.paused = False
        self.gain_db = 0.0
        # Время правки указателя трека, который уже играет: по нему ловится ЗАМЕНА
        # (шумодав досчитал весь клип) и не читается тот же файл второй раз.
        self.seen = 0.0


def _drain(commands: queue.Queue[dict[str, Any]], state: PlayState,
           extra: dict[str, Any]) -> None:
    """Разобрать накопившиеся команды; неизвестные/битые — мимо.

    Команды не копятся: разбираем ВСЁ, что пришло, и берём последнее положение.
    Пока разбираем — состояние трека не читаем: так просьба «встань на 300 с»
    (перемотка по таймлайну) не превращается в проигрывание всех промежуточных
    мест, а именно встаёт на названное.
    """
    while True:
        try:
            cmd = commands.get_nowait()
        except queue.Empty:
            break
        name = str(cmd.get("cmd") or "")
        if name == "pause":
            state.paused = True
            continue
        if name in ("play", "seek"):
            try:
                at = float(cmd.get("at") or 0.0)
            except (TypeError, ValueError):
                continue
            state.pos = max(0, min(int(round(at * SR)), state.total))
            state.paused = False
            # После перевода позиции состояние плагинов сбрасываем: у ревербератора
            # хвост от прежнего места звучал бы поверх нового — «эхо из прошлого».
            extra["reset"] = True
            continue
        if name in ("gain", "volume"):
            try:
                state.gain_db = float(cmd.get("db", cmd.get("gain", 0.0)))
            except (TypeError, ValueError):
                pass                    # кривое значение громкости — оставляем прежнюю
            continue
        if name == "track":
            extra["new_track"] = cmd
            continue
        if name == "stop":
            extra["stop"] = True
            continue


def _follow_track(path: str, state: PlayState) -> tuple[Any, float] | None:
    """Новый трек, если указатель изменился; иначе None.

    Файл-указатель пишет родитель (атомарно), здесь только читаем: пока шумодав
    считает, указатель пуст. `state.seen` — время правки уже игранного указателя:
    по нему ловим ЗАМЕНУ трека, не читая и не грузя WAV на каждом шаге.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        mtime = os.path.getmtime(path)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or mtime <= state.seen:
        return None
    track = str(data.get("track") or "")
    if not track or not os.path.isfile(track):
        return None
    return read_fragment(track), mtime


def _swap_track(track: Any, old: PlayState, at: Any, track_path: str,
                seen: float | None = None) -> tuple[Any, PlayState]:
    """Заменить играющий трек: пауза и место сохраняются, если место не названо.

    Замена бывает в живой игре (шумодав досчитался или сменили его настройки), и
    прыгать при этом нельзя: голос клипа — та же дорожка времени, только с другим
    шумодавом, поэтому позиция и пауза остаются прежними. Названное место (`at`)
    берётся, только когда его прислал родитель — первый готовый трек ставит стартовое.

    `seen` — время правки указателя, УЖЕ учтённое: без него `_follow_track` увидел бы
    тот же указатель «новым» на следующем блоке и второй раз перезагрузил трек,
    вернув позицию в ноль.
    """
    state = PlayState(int(track.shape[-1]))
    state.paused = old.paused
    state.gain_db = old.gain_db
    pos = old.pos
    if at is not None:
        try:
            pos = int(round(float(at) * SR))
        except (TypeError, ValueError):
            pos = old.pos
    state.pos = max(0, min(pos, state.total))
    if seen is None:
        try:
            seen = os.path.getmtime(track_path) if track_path else 0.0
        except OSError:
            seen = 0.0
    state.seen = seen
    return track, state


def _chunk(frag: Any, state: PlayState, size: int) -> Any:
    """Кусок трека с позиции: (каналы, сэмплы) — копия, плагины правят на месте."""
    a = state.pos
    b = min(a + size, state.total)
    return frag[:, a:b].copy()


class BoardRef:
    """Доска цепочки, которую можно ЗАМЕНИТЬ на лету.

    Правка цепочки (добавить/убрать/вкл/выкл/порядок) приходит командой `chain`,
    когда звук уже играет. Пересоздавать поток вывода ради этого нельзя: это щелчок
    и задержка на открытие устройства. Поэтому рабочему потоку отдаётся ЭТА обёртка,
    а новая `Pedalboard` подменяется внутри неё — поток остаётся тем же.
    """

    def __init__(self, board: Any) -> None:
        self.board = board

    def __call__(self, chunk: Any, sr: float, reset: bool = True) -> Any:
        return self.board(chunk, sr, reset=reset)


# Команды звука: их разбирает рабочий поток (`play_live`), и они НЕ ждут, пока
# главный поток закроет окно плагина. Всё остальное — управление хостом.
AUDIO_COMMANDS = ("play", "seek", "pause", "track", "gain", "volume")
# Правка цепочки идёт в ГЛАВНОМ потоке (очередь `control`): pedalboard требует, чтобы
# загрузка и «подготовка» плагина (первый прогон с частотой, каналами и размером блока,
# reset) шли в главном потоке процесса — иначе часть плагинов (Valhalla) отказывает:
# «must be reloaded on the main thread». Пока открыто окно плагина, главный поток занят
# `show_editor`, и перестройка ждёт его закрытия (родителю уходит событие
# `chain_deferred`). Звук при этом не замолкает: рабочий поток играет прежнюю доску.
CHAIN_COMMANDS = ("chain",)
# Окно плагина открыто прямо сейчас (главный поток внутри `show_editor`).
_WINDOW_OPEN = False
# Тишина, которой рабочий поток «вымывает» хвост ревербератора после перемотки
# (замена reset=True, которого из рабочего потока звать нельзя), 2 секунды.
FLUSH = 2 * SR


def _route_commands(commands: queue.Queue[dict[str, Any]],
                    audio: queue.Queue[dict[str, Any]],
                    control: queue.Queue[dict[str, Any]],
                    stop: threading.Event) -> None:
    """Развести команды по ДВУМ потребителям: звук (рабочий поток) и управление (главный).

    Одной очереди тут мало: окно плагина открывается ГЛАВНЫМ потоком и блокирует его
    до закрытия, а превью в это время продолжает слать play/seek. Если бы звуковые
    команды ждали в той же очереди, звук отстал бы от картинки ровно на время,
    которое человек крутит ручки, — «звук не прерывается» превратилось бы в «звук
    замирает». Правка цепочки идёт в главный поток (см. `CHAIN_COMMANDS`): если окно
    открыто, она отложена до его закрытия — об этом событие `chain_deferred`.

    `stop` (и конец трубы) — конец хоста: ставим событие, и все потоки замолкают.
    """
    while not stop.is_set():
        try:
            cmd = commands.get(timeout=IDLE_STEP)
        except queue.Empty:
            continue
        name = str(cmd.get("cmd") or "")
        if name == "stop":
            stop.set()
            return
        if name in AUDIO_COMMANDS:
            audio.put(cmd)
        else:
            if name in CHAIN_COMMANDS and _WINDOW_OPEN:
                _emit_event("chain_deferred")
                _note("цепочка будет перестроена после закрытия окна плагина")
            control.put(cmd)


def _write_state(state_out: str, raw: bytes) -> None:
    """Записать состояние плагина по закрытию окна (родитель прочитает по событию)."""
    if not state_out:
        return
    with open(state_out, "wb") as f:
        f.write(raw)


def _open_editor(loaded: list[tuple[dict[str, Any], Any]], index: int,
                 state_out: str, path: str = "") -> None:
    """Открыть окно плагина в УЖЕ ЗВУЧАЩЕМ хосте и забрать его состояние.

    Главный поток блокируется на `show_editor` до закрытия окна — так и надо: JUCE
    показывает окно только в главном потоке. Звук при этом не трогается вовсе: им
    живёт рабочий поток, а команды позиции идут к нему напрямую (см. `_route_commands`).

    События по краям нужны родителю: `editor_open` — поднять окно поверх всех
    (`core/voicefx_win` ждёт ПОЯВЛЕНИЯ окна, а не запуска процесса), `state` — новое
    состояние плагина для профиля спикера. Окно закрылось — хост продолжает играть:
    событие `editor_closed` не значит «конец сессии», сессию закрывает родитель.
    """
    global _WINDOW_OPEN
    target = open_target(loaded, index, path)
    _emit_event("editor_open", index=int(index))
    _WINDOW_OPEN = True
    try:
        target.show_editor()
    finally:
        _WINDOW_OPEN = False
    raw = bytes(target.raw_state)
    _write_state(state_out, raw)
    _emit_event("state", index=int(index),
                state_b64=base64.b64encode(raw).decode("ascii"))
    _emit_event("editor_closed", index=int(index))


def plugin_states(loaded: list[tuple[dict[str, Any], Any]]) -> list[dict[str, str]]:
    """Состояния ВСЕХ загруженных плагинов: `[{path, name, state_b64}, …]`.

    Одна дверь на оба события о состоянии (`state` по закрытию окна и `states` по
    команде `dump_states`) и на запись файла состояния: второй копии сбора быть не
    должно — иначе «состояние всех плагинов» и «состояние одного» разъедутся.

    Берутся ВСЕ загруженные, включая выключенные: выключенный плагин держат
    загруженным нарочно (включение галки не оплачивается вторыми секундами загрузки),
    и его ручки человек тоже крутит. Путь — ключ записи в профиле: у оболочек
    (WaveShell) имена повторяются, а путь один на строку панели и на задание.
    """
    out: list[dict[str, str]] = []
    for item, plug in loaded:
        path = str(item.get("path") or "")
        if not path:
            continue
        try:
            raw = bytes(plug.raw_state)
        except Exception:                             # noqa: BLE001 — чужой плагин не повод падать
            continue
        if not raw:
            continue
        out.append({"path": path, "name": str(item.get("name") or ""),
                    "state_b64": base64.b64encode(raw).decode("ascii")})
    return out


def _dump_states(loaded: list[tuple[dict[str, Any], Any]]) -> None:
    """Отдать состояние ВСЕХ загруженных плагинов событием `states`.

    Зовётся ПЕРЕД гашением хоста (сервер потом снимет процесс по PID): состояние
    знает только процесс хоста, и без этого события настройки, накрученные в окнах,
    пропали бы при «закрыл превью / сменил клип». Работает и когда окно уже закрыто:
    педалборд с командой `dump_states` ждёт события, и разбирать по одной записи
    профиля на каждый плагин было бы дороже, чем один раз весь список.
    """
    _emit_event("states", plugins=plugin_states(loaded))


def _apply_chain(pb: Any, loaded: list[tuple[dict[str, Any], Any]], ref: BoardRef,
                 items: list[dict[str, Any]]) -> None:
    """Перестроить цепочку на лету: новая доска в обёртке, поток не пересоздаётся.

    `loaded` правится НА МЕСТЕ (список один на все команды хоста): по нему открывается
    окно, и после перестройки индексы обязаны означать уже новую цепочку. Уже
    загруженные плагины берутся из неё же (`keep`), а не грузятся заново.
    """
    keep = {(str(it.get("path")), str(it.get("name") or "")): plug for it, plug in loaded}
    fresh, skipped = load_chain(pb, items, keep)
    ref.board = pb.Pedalboard([plug for item, plug in fresh if item.get("on") is True])
    loaded[:] = fresh
    _emit_event("chain", n=len(fresh),
                on=sum(1 for item, _plug in fresh if item.get("on") is True),
                skipped=skipped)


def _host_loop(pb: Any, loaded: list[tuple[dict[str, Any], Any]], ref: BoardRef,
               control: queue.Queue[dict[str, Any]], stop: threading.Event,
               state_out: str) -> None:
    """Главный цикл живого хоста: окна и правки цепочки — пока звук играет.

    Звук здесь не читается и не пишется: им занят рабочий поток. Главный поток ждёт
    управления — открыть окно, перестроить цепочку, закрыться. Окно (если просили)
    открывается ДО цикла: человек нажал «Настроить» и ждёт его, а не команду.
    """
    while not stop.is_set():
        try:
            cmd = control.get(timeout=IDLE_STEP)
        except queue.Empty:
            continue
        name = str(cmd.get("cmd") or "")
        if name == "quit":
            return
        if name == "dump_states":
            _dump_states(loaded)
            continue
        if name == "chain":
            _apply_chain(pb, loaded, ref, _chain_items(cmd))
            continue
        if name == "open_editor":
            try:
                index = int(cmd.get("index", -1))
            except (TypeError, ValueError):
                continue
            try:
                _open_editor(loaded, index, state_out, str(cmd.get("path") or ""))
            except ReelsiError as e:
                # Плагина нет в загруженных (он пропущен при загрузке) — это не повод
                # ронять звучащий хост: причина с именем уезжает родителю, панель
                # говорит «не загрузился — пропущен», а звук идёт дальше без него.
                _note(str(e))
                _emit_event("editor_failed", index=index, reason=str(e))


def play_live(frag: Any, board: Any, stream: _Stream, stop: threading.Event,
              on_error: Callable[[str], None], commands: queue.Queue[dict[str, Any]],
              *, track_path: str = "", start: float = 0.0,
              on_state: Callable[[float], None] | None = None,
              paused: bool = False,
              gain_db: float = 0.0) -> None:
    """Играть трек через цепочку по командам с сервера — до остановки.

    Блоки по 50 мс, `reset=False` ПОСЛЕ первого: сброс состояния на каждом блоке
    превратил бы ревербератор в обрубленную на каждом стыке кашу — «живой звук»
    звучал бы с артефактами, которых в запечённом треке нет.

    Игра идёт ВСЕГДА, пока открыто окно, а пауза не выключает звук: `paused` значит
    «стоим на месте», и вместо чтения трека в устройство уезжает тишина того же
    размера. Так поток не пересоздаётся на каждом «Играть» — пересоздание даёт
    щелчок и задержку на открытие устройства.

    Сбой записи (устройство отвалилось) не должен молча играть дальше: зовём
    `on_error`, и главный поток закрывает окно сам — иначе процесс висел бы с
    мёртвым звуком, а человек ждал бы его вечно.

    Свой трек читается из `frag` (numpy (каналы, сэмплы)), а замена — из
    `track_path`: так шумодав, досчитавший весь клип, попадает в звук без
    перезапуска окна.

    Частота записи — ЧАСТОТА УСТРОЙСТВА (`stream.sample_rate`), а не SR: у владельца
    устройство по умолчанию 44.1 кГц, и `write` с 48 падал RuntimeError'ом — живой
    хост замолкал на первой же записи. Плагины при этом считают на SR неизменно, а
    расхождение снимает поточный ресемплер на выходе цепочки.

    `paused` — начать НА ПАУЗЕ: живой хост превью поднимается раньше, чем человек
    нажал «Играть», а дорожка шумодава может досчитаться посреди паузы. Без этого
    хост заиграл бы сам в тот момент, когда трек появился, поверх стоящей картинки.
    Замена трека (`track` и указатель) паузу СОХРАНЯЕТ по той же причине.
    """
    import numpy as np
    if frag is None or int(frag.shape[-1]) <= 0:
        raise ReelsiError("Трек пустой — играть нечего")
    # Частота, в которой играет УСТРОЙСТВО, и ресемплер 48 → она. Плагины считают на
    # SR (поведение и звук при 48 кГц не меняются), а `write` уходит с частотой
    # устройства — иначе pedalboard бросает «sample rate does not match», и звук
    # пропадает целиком (владелец: устройство по умолчанию 44.1 кГц).
    out_rate = _out_rate(stream)
    resample = _make_resampler(out_rate)
    state = PlayState(int(frag.shape[-1]))
    state.paused = bool(paused)
    state.gain_db = float(gain_db)
    state.pos = max(0, min(int(round(max(0.0, float(start)) * SR)), state.total))
    # Указатель, по которому ЭТОТ трек и взят, уже «виден»: без этого первый же
    # кадр принял бы его за замену, перезагрузил трек и вернул позицию в ноль —
    # ровно то, из-за чего живой звук начинался бы не с места бегунка.
    if track_path:
        try:
            state.seen = os.path.getmtime(track_path)
        except OSError:
            state.seen = 0.0
    if on_state is not None:
        on_state(state.pos / float(SR))
    first = True
    chunk = 0
    while not stop.is_set():
        extra: dict[str, Any] = {}
        _drain(commands, state, extra)
        if extra.get("stop"):
            return
        new = extra.get("new_track")
        if isinstance(new, dict):
            path = str(new.get("path") or "")
            if path and os.path.isfile(path):
                track = read_fragment(path)
                if int(track.shape[-1]) > 0:
                    frag, state = _swap_track(track, state, new.get("at"), track_path)
                    extra["reset"] = True
        if not extra.get("reset") and track_path:
            followed = _follow_track(track_path, state)
            if followed is not None:
                frag, state = _swap_track(followed[0], state, None, track_path,
                                          seen=followed[1])
                extra["reset"] = True
        if state.paused or state.total <= 0:
            block = np.zeros((CHANNELS, BLOCK), dtype="float32")
            wrapped = False
        else:
            block = _chunk(frag, state, BLOCK)
            wrapped = state.pos == 0
            if wrapped:
                _fade(block, FADE, True)          # начало трека — из тишины
            if state.pos + block.shape[-1] >= state.total:
                _fade(block, FADE, False)         # конец трека — в тишину
        # reset=True из рабочего потока ЗВАТЬ НЕЛЬЗЯ: pedalboard требует перенастройку
        # плагина в главном потоке (Valhalla отказывает: «must be reloaded on the main
        # thread»). Плагины готовит главный поток при загрузке цепочки (`_prime`), а
        # хвост после перемотки вымываем тишиной — вывод этого прогона выбрасываем.
        try:
            if extra.get("reset") and not first:
                board(np.zeros((CHANNELS, FLUSH), dtype="float32"), SR, reset=False)
            first = False
            out = board(block, SR, reset=False)
        except Exception as e:                    # noqa: BLE001 — звук не повод ронять окно
            on_error(f"{type(e).__name__}: {e}")
            return
        try:
            out_gain = out if state.gain_db == 0.0 else (out * (10.0 ** (state.gain_db / 20.0)))
            if resample is not None:
                out_gain = resample(out_gain)          # 48 → частота устройства
            stream.write(out_gain, out_rate)
        except Exception as e:                    # noqa: BLE001 — звук не повод ронять окно
            on_error(f"{type(e).__name__}: {e}")
            return
        chunk += 1
        if on_state is not None and chunk % 20 == 0:
            on_state(state.pos / float(SR))
        if not state.paused and state.total > 0:
            state.pos += block.shape[-1]
            if state.pos >= state.total:
                state.pos = 0                     # конец трека — с начала
    return


def play_loop(frag: Any, board: Any, stream: _Stream, stop: threading.Event,
              on_error: Callable[[str], None]) -> None:
    """Играть фрагмент по кругу через цепочку до сигнала остановки.

    Тонкая обёртка над `play_live` без команд: так проверяется и звук, и стык —
    поведение ровно то же, что у живого режима, и второй копии цикла нет.
    """
    play_live(frag, board, stream, stop, on_error, queue.Queue())


def _close_quietly(stream: Any) -> None:
    """Закрыть поток, не давая закрытию перебить исходную ошибку (вход не состоялся)."""
    try:
        stream.close()
    except Exception:                         # noqa: BLE001 — закрытие уже мёртвого потока
        pass


def _exit_quietly(stream: _Stream) -> None:
    """Остановить поток, не давая остановке перебить исходную ошибку.

    Именно `__exit__`, а не один `close()`: у pedalboard выход — это stop + close
    разом (io/AudioStream.h), `close()` без `stop()` оставил бы поток запущенным.
    """
    try:
        stream.__exit__(None, None, None)
    except Exception:                         # noqa: BLE001 — остановка уже мёртвого потока
        pass


def _open_stream(device: str) -> _Stream | None:
    """Открыть и ЗАПУСТИТЬ поток вывода. Нет устройства/не открылся — None и строка в stderr.

    Звук здесь необязателен: человек пришёл настраивать плагин, и окно без звука
    полезнее, чем не открывшееся окно.

    Пустое имя — это не «без вывода»: у настоящего AudioStream `None` значит ровно
    отсутствие вывода, то есть тишину. Поэтому пустое заменяем на системное
    устройство по умолчанию (`AudioStream.default_output_device_name`).

    Запускаем тут же, до первой записи: созданный, но не запущенный поток
    (`running == False`) глотает `write` молча — замер владельца: 0.5 с звука
    «записалось» за 0.89 с, а из колонок не шло ничего. Вход и выход парные:
    не вошли — поток наружу не отдаём (и закрываем его сами, иначе устройство
    осталось бы занятым), вошли — `__exit__` зовёт рабочая функция в своём `finally`.

    Не приняли 48 кГц — открываем в частоте САМОГО устройства (`sample_rate=None`):
    часть устройств (владелец: 44.1 кГц по умолчанию) другой частоты не даёт вовсе.
    Ресемплер на выходе цепочки доведёт звук, а окно откроется со звуком, а не в тишине.
    """
    stream: Any = None
    try:
        pb = _pedalboard()
        cls = pb.io.AudioStream
        name = device or cls.default_output_device_name
        try:
            stream = cls(output_device_name=name, sample_rate=SR, buffer_size=BUFFER,
                         num_output_channels=CHANNELS)
            try:
                stream.__enter__()
            except BaseException:             # в том числе Ctrl-C посреди запуска
                _close_quietly(stream)        # вход не состоялся — отпускаем устройство
                raise
        except Exception as e:                # noqa: BLE001 — устройство не приняло частоту трека
            _note(f"устройство не приняло {SR} Гц ({type(e).__name__}: {e}) — "
                  f"открываю в частоте устройства")
            if stream is not None:
                _close_quietly(stream)        # вход не состоялся — отпускаем устройство
            stream = cls(output_device_name=name, sample_rate=None,
                         buffer_size=BUFFER, num_output_channels=CHANNELS)
            stream.__enter__()
    except Exception as e:                    # noqa: BLE001 — звук не повод не открывать окно
        _note(f"звука нет ({type(e).__name__}: {e}) — окно открыто без прослушивания")
        # Причина — СОБЫТИЕМ: иначе страница глушит свой голос в пользу хоста, который
        # молчит, и человек слышит полную тишину.
        _emit_event("audio_error", reason=f"{type(e).__name__}: {e}")
        return None
    return cast(_Stream, stream)


def _commands_from_stdin(commands: queue.Queue[dict[str, Any]],
                         stop: threading.Event, stream: TextIO) -> None:
    """Читать команды из stdin в очередь — фоновым потоком.

    Отдельным потоком, а не в цикле звука: чтение трубы блокирующее, и ждать его
    в блоке обработки значило бы «окно открыто, звук стоит». Конец трубы (сервер
    ушёл, окно закрыто) ставит `stop`: трек замолкает сам, и процесс окна не
    играет в пустую комнату.
    """
    try:
        for line in stream:
            text = line.strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except ValueError:
                continue                      # посторонняя строка — не команда
            if isinstance(data, dict):
                commands.put(data)
    except Exception:                         # noqa: BLE001 — труба оборвалась
        pass  # труба закрылась на середине строки — ниже ставим stop
    stop.set()


def _track_ready(job: dict[str, Any]) -> tuple[Any, str]:
    """Готовый трек из указателя: (фрагмент, путь) или (None, "") — ещё считается.

    Пустой/битый указатель — не ошибка: родитель пишет его, когда шумодав
    досчитал. Загрузка WAV — самая долгая операция живого режима, поэтому она
    идёт ДО первой команды, а окно к этому моменту уже открыто.
    """
    path = str(job.get("track_file") or "")
    if not path:
        return None, ""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, ""
    track = str(data.get("track") or "") if isinstance(data, dict) else ""
    if not track or not os.path.isfile(track):
        return None, ""
    return read_fragment(track), path


def execute_live(job: dict[str, Any], state_out: str) -> None:
    """Живой режим: хост звука, окна по команде, цепочка на лету, запись состояния.

    Хост — ОДИН процесс на клип, и окно ему не обязательно: `index = -1` (или пустой
    `index`) значит «подними звук, окна не показывай». Так хост и живёт всё время,
    пока открыто превью: голос всегда идёт через цепочку вживую, а «Настроить» лишь
    показывает панель уже звучащего хоста (команда `open_editor`) — звук при этом не
    прерывается и процесс не перезапускается.

    Порядок важен: цепочка грузится ДО окна (состояния ставятся на сам объект
    плагина — тот же, что открывается и звучит), команды читаются с первого мгновения
    (иначе перемотка в первые полсекунды потерялась бы), а окно показывается
    последним — так всё, что нужно для звука, готово к моменту, когда человек взялся
    за ручку.
    """
    global _EVENTS_FILE
    _EVENTS_FILE = str(job.get("events_file") or "")
    pb = _pedalboard()
    loaded, skipped = load_chain(pb, _chain_items(job))
    # Что реально встало в цепочку — родителю сразу: по этому событию панель «Голос»
    # говорит «Bertom_DenoiserClassic не загрузился — пропущен», не дожидаясь правок.
    _emit_event("chain", n=len(loaded),
                on=sum(1 for item, _plug in loaded if item.get("on") is True),
                skipped=skipped)
    index = int(job.get("index", -1))
    if not loaded:
        # Ни одного плагина не встало: живой хост не нужен вовсе — звук превью идёт
        # дорожкой шумодава. Родитель по этому событию снимет хост, а окно (если его
        # просили) показать всё равно надо: человек пришёл крутить ручки.
        _emit_event("empty")
        if index < 0:
            # Окна не просили, играть нечем и не через что: процесс не занимает
            # устройство и не висит зря — превью играет дорожку шумодава в браузере.
            _note("ни один плагин цепочки не загрузился — живой хост не нужен")
            _finish_live(state_out)
            return
    ref = BoardRef(pb.Pedalboard([plug for item, plug in loaded if item.get("on") is True]))

    commands: queue.Queue[dict[str, Any]] = queue.Queue()
    audio: queue.Queue[dict[str, Any]] = queue.Queue()
    control: queue.Queue[dict[str, Any]] = queue.Queue()
    stop = threading.Event()
    failed: list[str] = []
    stream: _Stream | None = None
    worker: threading.Thread | None = None
    try:
        threading.Thread(target=_commands_from_stdin,
                         args=(commands, stop, sys.stdin),
                         name="voicefx-stdin", daemon=True).start()
        threading.Thread(target=_route_commands,
                         args=(commands, audio, control, stop),
                         name="voicefx-route", daemon=True).start()
        stream = _open_stream(str(job.get("device") or ""))
        if stream is not None:
            worker = threading.Thread(
                target=_prepared_play,
                args=(job, ref, stream, stop, failed, audio),
                name="voicefx-live", daemon=True)
            worker.start()
        if 0 <= index < len(loaded):
            _open_editor(loaded, index, state_out)   # блокирует главный поток до закрытия окна
        _host_loop(pb, loaded, ref, control, stop, state_out)
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=5.0)
        if stream is not None:
            _exit_quietly(stream)
    _finish_live(state_out)


def _finish_live(state_out: str) -> None:
    """Конец живого хоста: файл состояния обязателен, а прежнее состояние не трогаем.

    Пустой файл создаём, только если его нет вовсе: родитель по нему понимает, что
    хост ушёл сам, а не был снят по PID. Уже записанное состояние (окно закрыли,
    хост играет дальше) НЕ затираем — по нему сервер узнаёт настройки плагина, и
    стереть его значило бы потерять только что накрученное.
    """
    if not state_out:
        raise ReelsiError("Не указан файл состояния (--state-out)")
    if not os.path.exists(state_out):
        with open(state_out, "wb") as f:
            f.write(b"")


def _prepared_play(job: dict[str, Any], board: Any, stream: _Stream,
                   stop: threading.Event, failed: list[str],
                   commands: queue.Queue[dict[str, Any]]) -> None:
    """Дождаться готового трека и играть его по командам (рабочий поток).

    Ждём трек ЗДЕСЬ, а окно уже открыто: шумодав считает голос клипа минутами, и
    держать окно в ожидании значило бы вернуть ту самую жалобу «открывается долго».
    Пока трека нет, команды копятся в очереди — первая же из них сдвинет игру на
    названное место, как только трек появится.

    Трек грузится ОДИН раз: замена (шумодав досчитал весь клип) приходит командой
    `track` уже в цикле. Так первый запуск не зависит от того, успел ли родитель
    записать полный трек, а звук не начинается с середины.
    """
    track = None
    path = str(job.get("track_file") or "")
    # Ждём до остановки (окно закрыли) — потолок тут не нужен: пока окно открыто,
    # родитель вправе досчитать трек в любой момент, а вечно висящий поток живёт
    # только вместе с процессом окна и гаснет по `stop`.
    while not stop.is_set() and track is None:
        track, path = _track_ready(job)
        if track is not None:
            break
        stop.wait(TRACK_POLL)
    if track is None:
        # Трека нет вовсе (родитель ушёл, пока считалось) — играть нечего, но окно
        # и ручки остаются: человек настраивает плагин, а не слушает.
        _note("трека клипа нет — окно открыто без прослушивания")
        return

    def _on_audio_error(reason: str) -> None:
        """Сбой звука — строкой в stderr и событием: страница вернёт звук себе."""
        _audio_error_note(failed, reason)

    try:
        play_live(track, board, stream, stop, _on_audio_error, commands,
                  track_path=path, start=float(job.get("start") or 0.0),
                  paused=bool(job.get("paused")),
                  gain_db=float(job.get("gain_db", 0.0) or 0.0))
    except (ReelsiError, ValueError, TypeError) as e:   # пустой трек — не повод ронять окно
        _note(str(e))


def execute_job(job: dict[str, Any], state_out: str) -> None:
    """Открыть окно по заданию: фрагмент по кругу, цепочка, запись состояния.

    Прежний режим задания (без `--live`): играет `frag` — короткий кусок клипа.
    Оставлен для проверок и совместимости; интерфейс пользуется `execute_live`,
    где играет трек всего клипа и едет вместе с превью.
    """
    frag_path = str(job.get("frag") or job.get("track") or "")
    if not frag_path:
        raise ReelsiError("В задании нет фрагмента (frag)")
    pb = _pedalboard()
    loaded, skipped = load_chain(pb, _chain_items(job))
    for miss in skipped:
        _note(f"плагин {miss['name']} не загрузился — пропущен")
    target = open_target(loaded, int(job.get("index", -1)))
    in_sound = [plug for item, plug in loaded if item.get("on") is True]
    board = pb.Pedalboard(in_sound)
    frag = read_fragment(frag_path)

    stop = threading.Event()
    failed: list[str] = []
    stream: _Stream | None = None
    worker: threading.Thread | None = None
    try:
        stream = _open_stream(str(job.get("device") or ""))
        if stream is not None:
            worker = threading.Thread(
                target=play_loop,
                args=(frag, board, stream, stop,
                      lambda reason: _audio_error_note(failed, reason)),
                name="voicefx-live", daemon=True)
            worker.start()
        target.show_editor()              # блокирует главный поток до закрытия окна
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=5.0)
        if stream is not None:
            _exit_quietly(stream)
    if not state_out:
        raise ReelsiError("Не указан файл состояния (--state-out)")
    with open(state_out, "wb") as f:
        f.write(bytes(target.raw_state))


def _fail(e: BaseException) -> NoReturn:
    """Ошибка процесса наружу: JSON-объектом с кодом, а не строкой в stderr.

    Не `core.umsg.cli_error`: тот печатает текст в stderr и теряет КОД перевода —
    родитель ждёт его из stdout (core/voicefx_proc.fail). Контракт тот же:
    `except ReelsiError` на точке входа, сообщение пользователю и код 1.
    """
    voicefx_proc.fail(e)


def _run_job(job_path: str, state_out: str, live: bool = False) -> None:
    """Режим `--job`: задание файлом; `live` — трек клипа по командам, иначе фрагмент."""
    job = read_job(job_path)
    if live:
        execute_live(job, state_out)
        return
    execute_job(job, state_out)


def main(argv: Sequence[str] | None = None) -> int:
    """Открыть окно плагина и записать его состояние в --state-out."""
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if args["job"]:
        _run_job(args["job"], args["state-out"], live=bool(args["live"]))
        return 0
    if not args["path"]:
        raise ReelsiError("Не указан путь к плагину (--path)")
    plugin = load_plugin(args["path"], args["name"])
    state_in = args["state-in"]
    if state_in and os.path.isfile(state_in):
        with open(state_in, "rb") as f:
            plugin.raw_state = f.read()
    plugin.show_editor()                # блокирует главный поток до закрытия окна
    state_out = args["state-out"]
    if not state_out:
        raise ReelsiError("Не указан файл состояния (--state-out)")
    with open(state_out, "wb") as f:
        f.write(bytes(plugin.raw_state))
    return 0


if __name__ == "__main__":
    # Окна ошибок Windows и UTF-8 — ДО чужого плагина: показать своё окно плагин
    # вправе (его рисует он сам), а модальное «Application Error» от системы
    # осталось бы висеть на экране и держало процесс (см. core/voicefx_proc.py).
    voicefx_proc.no_error_windows()
    voicefx_proc.utf8_stdout()
    try:
        sys.exit(main())
    except ReelsiError as e:
        _fail(e)
