# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Цепочка VST3 — ОТДЕЛЬНЫМ процессом (запускает core/voicefx.py).

Зачем отдельный процесс. `load_plugin` открывает DLL плагина, а JUCE поднимает
внутри неё свои фоновые потоки и не останавливает их до выгрузки библиотеки,
которой из живого процесса не бывает. В сервере интерфейса это выглядело так:
`GET /api/voicefx_vst_list` — 13 с ответа, дальше ~1500 % процессора и +23 потока
навсегда. Поэтому чужой плагин живёт здесь: процесс грузит цепочку, обрабатывает
WAV, печатает ход работы и уходит вместе со всеми потоками.

    python -m core.voicefx_render --job job.json

Задание (пишет core/voicefx._apply_vst) — JSON:

    {"src": WAV на входе, "out": WAV на выходе,
     "chain": [{"path","name","state_b64","on"} …]}   # состояние — base64

Обработка ПОТОКОВАЯ, блоками по секунде (как было в сервере): трек бывает на
час, а час стерео в памяти — это свыше гигабайта. Одна Pedalboard на всю цепочку
(а не по плагину на вызов) — так состояние между блоками живёт внутри плагинов,
как в реальном времени.

Ход работы — строки JSON в stdout: {"msg"} — строка лога, {"done", "frames"} —
конец работы, {"error", "text"} — ошибка (её текст родитель показывает
пользователю). Родитель переводит их в общий лог и снимает процесс по PID, если
работу отменили или она зависла. Общая обвязка процессов с чужим плагином —
core/voicefx_proc.py (системные окна ошибок Windows, UTF-8, ошибка кодом).
"""
from __future__ import annotations
import base64
import json
import os
import sys
import wave
from typing import IO, Any, Callable, NoReturn, Sequence

from core import voicefx_proc
from core.fileio import atomic_stream_write
from core.umsg import ReelsiError, umsg

# Блок потоковой обработки VST — тот же, что был в сервере: секунда это
# компромисс между памятью (час стерео float32 — сотни мегабайт) и плагинами с
# хвостом, которым мелкий блок рвёт хвост на каждом стыке.
BLOCK_SECONDS = 1.0

emit = voicefx_proc.emit


def pedalboard() -> Any:
    """Ленивый импорт pedalboard: без пакета VST недоступны вовсе.

    Проверяются оба вида «пакета нет»: ImportError и `sys.modules[…] = None`
    (так отсутствие пакета подменяют тесты — `import` в этом случае тоже бросает
    ImportError, но полагаться на одну ветку нельзя: у `None` нет ни одного
    атрибута, и обращение к нему упало бы уже в чужом коде). Найденный модуль
    кладём в `found`, а не в `pb`: mypy сузил бы тип имени `pb` до модуля и
    ругался бы на `None` в ветке ImportError.
    """
    try:
        import pedalboard as pb
    except ImportError:
        # Так отсутствие пакета подменяют тесты (`sys.modules[...] = None`):
        # импорт в этом случае тоже бросает ImportError, но у `None` нет ни одного
        # атрибута, и обращение к нему упало бы уже в чужом коде.
        found = getattr(sys.modules, "pedalboard", None)
    else:
        found = pb
    if found is None:
        raise ReelsiError(umsg("vst_unavailable",
                               "Нет пакета pedalboard — VST-плагины недоступны: "
                               "pip install pedalboard"))
    return found


def read_job(path: str) -> dict[str, Any]:
    """Задание из файла; битый/чужой файл — понятная ошибка, не падение."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ReelsiError(f"Файл задания не прочитан ({path}): {e}")
    if not isinstance(data, dict):
        raise ReelsiError(f"Файл задания не объект: {path}")
    return data


def chain_items(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Включённые элементы цепочки из задания; битые отбрасываются.

    Включённость проверяет родитель, но задание — файл на диске: чужой или
    правленый руками файл не должен подсунуть в звук выключенный плагин.
    """
    raw = job.get("chain")
    out: list[dict[str, Any]] = []
    for item in (raw if isinstance(raw, list) else []):
        if not isinstance(item, dict) or not str(item.get("path") or "").strip():
            continue
        if item.get("on") is not True:
            continue
        out.append(item)
    return out


def load_chain(pb: Any, chain: list[dict[str, Any]]) -> tuple[list[Any], list[dict[str, str]]]:
    """Загрузить плагины цепочки по порядку; вернуть (загруженные, пропущенные).

    Состояние из base64 ставим сразу: без него плагин отработал бы с заводскими
    ручками, и человек услышал бы не то, что настроил.

    Незагружаемый плагин НЕ роняет цепочку: он пропускается, причина с его
    ИМЕНЕМ уезжает в `skipped`, и цепочка работает без него. Человеку его
    показывает панель «Голос» («Bertom_DenoiserClassic не загрузился — пропущен»).
    """
    out: list[Any] = []
    skipped: list[dict[str, str]] = []
    for item in chain:
        name = str(item.get("name") or "")
        title = name or os.path.splitext(os.path.basename(str(item["path"])))[0]
        try:
            plug = pb.load_plugin(str(item["path"]), plugin_name=name or None)
        except Exception as exc:  # noqa: BLE001 — чужой плагин не повод ронять цепочку
            reason = str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
            skipped.append({"path": str(item["path"]), "name": title, "reason": reason})
            emit("Голос: плагин {name} не загрузился — пропущен ({reason})",
                 name=title, reason=reason)
            continue
        state = item.get("state_b64")
        if isinstance(state, str) and state:
            raw = base64.b64decode(state)
            if raw:
                plug.raw_state = raw    # состояние из профиля: то, что записало окно плагина
        out.append(plug)
    return out, skipped


def process_file(board: Any, src: str, out_wav: str, pb: Any) -> int:
    """Прогнать WAV через цепочку блоками по секунде; вернуть число кадров.

    Блоков в час — 3600, и на каждый приходит строка прогресса: родитель
    показывает её в логе, а не ждёт молча минуты.

    Моно-исходник дублируется в 2 канала ПЕРЕД цепочкой: стерео-плагины
    (ValhallaVintageVerb, ревербы) требуют 2 канала на входе и на выходе,
    а голос камеры бывает и моно. Результат пишется с ТАМЖЕ числом каналов,
    что и исходник (контракт `.voice.wav` не менялся): после цепочки стерео
    усредняем обратно в моно.

    Пишем через `fileio.atomic_stream_write`: tmp рядом с целью, fsync и
    `os.replace` — одной точкой записи на весь проект, а не своей копией. Сбой
    плагина на середине не должен оставить на месте готового трека половину
    звука (и не должен оставлять огрызок: `atomic_stream_write` убирает tmp сам).

    Файл пишет pedalboard, поэтому ему отдаётся открытый tmp-файл: `AudioFile`
    принимает file-like объект, и имя временного файла выбирает `fileio`, а не мы
    (`out_wav + ".part"` рядом с целью оставлял бы мусор при аварии).
    """
    import numpy as np  # тяжёлый импорт — только здесь, не в модуле

    written = 0

    def _write(f: IO[Any]) -> None:
        nonlocal written
        first = True
        with pb.io.AudioFile(src) as fin:
            sr = float(fin.samplerate)
            src_channels = int(fin.num_channels)
            # Цепочка всегда в стерео: моно → 2 канала, стерео — как есть.
            proc_channels = max(2, src_channels)
            block = max(1, int(sr * BLOCK_SECONDS))
            total = int(fin.frames)
            with pb.io.AudioFile(f, "w", sr, src_channels, format="wav") as fout:
                while fin.tell() < fin.frames:
                    chunk = fin.read(block)
                    if chunk.shape[-1] == 0:
                        break           # файл кончился (последний блок короче)
                    # Моно → стерео: дублируем канал
                    if chunk.shape[0] < proc_channels:
                        chunk = np.repeat(chunk, proc_channels, axis=0)
                    processed = board.process(chunk, sr, reset=first)
                    # Стерео → моно: усредняем, если исходник был моно
                    if processed.shape[0] > src_channels:
                        processed = np.mean(processed, axis=0, keepdims=True).astype(processed.dtype)
                    fout.write(processed)
                    written += int(chunk.shape[-1])
                    if total:
                        emit("Голос: VST — {pct} %", pct=int(written * 100 / total))
                    first = False

    atomic_stream_write(out_wav, _write)
    return written


def duration(path: str) -> float:
    """Длительность WAV в секундах (0.0 — файл не читается как WAV).

    Своими руками через wave, а не ffprobe: на вход сюда приходит только наш же
    извлечённый PCM, а лишний процесс на каждый рендер ни к чему.
    """
    try:
        with wave.open(path, "rb") as w:
            rate = w.getframerate() or 1
            return w.getnframes() / float(rate)
    except (OSError, EOFError, wave.Error):
        return 0.0


def close_to_source(src: str, out_wav: str,
                    emit_fn: Callable[..., Any] = emit) -> None:
    """Добить или подрезать результат до длительности входа.

    Задержку самих плагинов не компенсируем (pedalboard отдаёт
    `reported_latency_samples`, но сверить её на живом плагине нечем) — а вот
    длину держим: по запечённому голосу режет нарезка, и он же уезжает в итоговый
    трек. «Голос кончился на 30 мс раньше картинки» — ошибка, которую потом ищут
    руками в After Effects.

    Недобор добиваем тишиной, перебор — обрезаем: сдвинуть чужой плагин мы не
    можем, а длину вернуть обязаны.
    """
    want = duration(src)
    if want <= 0:
        return                        # вход не читается — сверять не с чем
    try:
        with wave.open(out_wav, "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            have = w.getnframes()
    except (OSError, EOFError, wave.Error):
        return                        # не WAV — рендер не валим
    need = int(round(want * rate))
    if need == have:
        return
    if need < have:
        _rewrite(out_wav, channels, width, rate, need, b"", emit_fn)
        emit_fn("Голос: длина подрезана до {ms:.0f} мс (плагин отдал больше входа)",
                ms=(have - need) / rate * 1000)
        return
    silence = b"\x00" * ((need - have) * channels * width)
    _rewrite(out_wav, channels, width, rate, have, silence, emit_fn)
    emit_fn("Голос: хвост {ms:.0f} мс добит тишиной до длины входа",
            ms=(need - have) / rate * 1000)


def _rewrite(path: str, channels: int, width: int, rate: int, keep: int,
             tail: bytes, emit_fn: Callable[..., Any]) -> None:
    """Переписать WAV: `keep` кадров из него плюс `tail` байт в конец.

    Через `fileio.atomic_stream_write` (tmp + fsync + `os.replace`): обрезать файл
    на месте (`truncate`) можно только по границе кадра, а нам нужен ещё и добор
    тишины — значит, нужен второй файл, и писать его мимо общей точки записи
    нельзя (tests/test_infra_dedup.py).
    """
    def _write(f: IO[Any]) -> None:
        with wave.open(path, "rb") as r, wave.open(f, "wb") as o:
            o.setnchannels(channels)
            o.setsampwidth(width)
            o.setframerate(rate)
            left = keep
            while left > 0:
                chunk = r.readframes(min(rate, left))     # по секунде: файл бывает на час
                if not chunk:
                    break
                o.writeframes(chunk)
                left -= len(chunk) // (channels * width)
            o.writeframes(tail)

    atomic_stream_write(path, _write)


def apply_job(job: dict[str, Any]) -> tuple[int, list[dict[str, str]]]:
    """Обработать WAV по заданию; вернуть (число кадров, пропущенные плагины).

    Пропущенные — те, что не загрузились: причина с ИМЕНЕМ плагина уезжает
    вызывающему, а цепочка работает без них. Все пропущены — выход равен входу,
    как будто цепочки нет (а не ошибка: раньше «код возврата 1» ронял всё).
    """
    src = str(job.get("src") or "")
    out_wav = str(job.get("out") or "")
    if not src or not out_wav:
        raise ReelsiError("В задании нет входа (src) или выхода (out)")
    if not os.path.isfile(src):
        raise ReelsiError(umsg("file_not_found", f"Файл не найден: {src}", path=src))
    chain = chain_items(job)
    if not chain:
        raise ReelsiError("В задании нет включённых плагинов (chain)")
    pb = pedalboard()
    plugins, skipped = load_chain(pb, chain)
    if not plugins:
        # Все плагины пропущены — выход = вход, цепочка пуста
        import shutil
        out_dir = os.path.dirname(os.path.abspath(out_wav))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        shutil.copyfile(src, out_wav)
        return 0, skipped
    emit("Голос: VST-плагинов в цепочке — {n}", n=len(plugins))
    board = pb.Pedalboard(plugins)
    out_dir = os.path.dirname(os.path.abspath(out_wav))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    written = process_file(board, src, out_wav, pb)
    close_to_source(src, out_wav)
    return written, skipped


def _fail(e: BaseException) -> NoReturn:
    """Ошибка процесса: JSON-строкой в stdout — чтобы родитель узнал КОД.

    `core.umsg.cli_error` печатает только текст: код перевода потерялся бы, и
    англоязычный пользователь увидел бы русское сообщение (та же причина, по
    которой коды объявлены в api/voicefx.py).
    """
    voicefx_proc.fail(e)


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа процесса: `--job job.json`."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0].lstrip("-") != "job":
        raise ReelsiError("Запуск: python -m core.voicefx_render --job job.json")
    job = read_job(argv[1])
    frames, skipped = apply_job(job)
    print(json.dumps({"done": True, "frames": frames, "skipped": skipped}), flush=True)
    return 0


if __name__ == "__main__":
    # Окна ошибок Windows и UTF-8 — ДО чужого плагина (pedalboard грузится внутри
    # apply_job): падение плагина обязано закончиться молча, а не модальным
    # «Application Error», которое держит процесс до нажатия кнопки.
    voicefx_proc.no_error_windows()
    voicefx_proc.utf8_stdout()
    try:
        sys.exit(main())
    except ReelsiError as e:
        _fail(e)
