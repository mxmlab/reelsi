# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Роуты обработки голоса спикера: список VST3, устройства вывода, окно плагина,
живой звук в нём, прослушивание, запекание голоса клипа, состояние шумодава RoFormer.

Ядро — `core/voicefx.py` (шумодав deep-filter и RoFormer, цепочка VST3, кеш
запечённого трека), здесь только HTTP: разобрать тело, позвать ядро, отдать пути
WAV — фронт играет их через `/api/media` («wav» там в разрешённых расширениях).
Окружение RoFormer (venv с audio-separator и модели) — `core/voicefx_sep.py`: его
состояние и фоновая установка тоже отдаются отсюда.

Настройки берутся из профиля спикера (`voice_fx`) и приходят телом запроса: роут
их не хранит — источник правды один, профиль. Какие настройки считать включёнными,
решает ОДНА функция ядра (`core.voicefx.voice_fx_on`): отдельной галки «в итоговый
трек» нет, включённый ИИ-шумодав работает везде и весь.

Живое прослушивание в окне плагина (`src` в `/api/voicefx_edit`) собирает ядро:
процесс окна едет отдельно от HTTP-запроса (тот возвращается СРАЗУ), звук играет
он сам (JUCE требует главный поток — Flask живёт в рабочих), а команды
«играть/пауза/перемотать» ему передаёт `/api/voicefx_live`. Окно закрыли —
состояние плагина СРАЗУ уезжает в профиль спикера (`_save_live`): отдельной кнопки
«Сохранить у спикера» больше нет, и голос клипа пересчитывается сам.

Запекание голоса клипа (`/api/voicefx_bake`) — фоновое задание ЭТОГО модуля: голос
всего клипа считается минутами (RoFormer — своим окружением), а превью играет
исходный звук, пока идёт счёт. Ход работы — теми же процентами, что печатает сам
RoFormer (`N/M`), а не «прокси 0 %».

Задание — на КЛИП, а не одно на сервер (VOICEJOBS, словарь по `xml`). Считается
шумодав по одному — видеокарта одна, и замок её уже есть, — но остальные клипы ждут
В ОЧЕРЕДИ со своим ходом: открыл клип A, ушёл в B, вернулся в A — у каждого свой
процент, и готовый трек клипа A никогда не уезжает клипу B. «Стоп»
(`/api/voicefx_bake_cancel`) снимает из очереди или гасит текущий счёт по PID
дочернего процесса, который запустили мы же.
"""
import math
import os
import threading
import time
from collections.abc import Mapping
from typing import Any, Callable, TypedDict

from flask import Response, jsonify, request
from ._core import bp, jstr, log_entry, umsg_err
from core.jobstate import kill_pid
from core.umsg import ReelsiError, umsg

# Коды ошибок, которые рождаются в ЯДРЕ (core/voicefx.py, core/voicefx_sep.py),
# объявлены здесь ещё раз — и это не копипаста текстов: сообщение берётся из самой
# ошибки (`str(e)`), здесь только код. Так надо потому, что словарь переводов
# (static/i18n/en.json) и его сторож (tests/test_i18n.py) собирают коды из `api/` и
# `core/aicut|xml2ae`: код, живущий лишь в core/voicefx.py, остался бы без перевода,
# и англоязычный пользователь увидел бы русский текст. Переменные (`dir` и прочие)
# едут как есть — по ним подставляется перевод ERR_<код> на фронте
# (static/app/00-core.js:errText).
_FORWARDED: dict[str, Callable[[ReelsiError], ReelsiError]] = {
    "deepfilter_missing": lambda e: ReelsiError(umsg("deepfilter_missing", str(e), **e.vars)),
    "deepfilter_failed": lambda e: ReelsiError(umsg("deepfilter_failed", str(e), **e.vars)),
    "roformer_missing": lambda e: ReelsiError(umsg("roformer_missing", str(e), **e.vars)),
    "roformer_failed": lambda e: ReelsiError(umsg("roformer_failed", str(e), **e.vars)),
    "voicefx_sep_install_failed": lambda e: ReelsiError(umsg("voicefx_sep_install_failed", str(e), **e.vars)),
    "vst_unavailable": lambda e: ReelsiError(umsg("vst_unavailable", str(e), **e.vars)),
    "voicefx_render_failed": lambda e: ReelsiError(umsg("voicefx_render_failed", str(e), **e.vars)),
    "voicefx_edit_failed": lambda e: ReelsiError(umsg("voicefx_edit_failed", str(e), **e.vars)),
    "voicefx_devices_failed": lambda e: ReelsiError(umsg("voicefx_devices_failed", str(e), **e.vars)),
    "voicefx_no_window": lambda e: ReelsiError(umsg("voicefx_no_window", str(e), **e.vars)),
    "voicefx_cancelled": lambda e: ReelsiError(umsg("voicefx_cancelled", str(e), **e.vars)),
}


def _forward(e: ReelsiError) -> ReelsiError:
    """Ошибка ядра с известным кодом — наверх тем же кодом (по нему ищется перевод)."""
    make = _FORWARDED.get(e.code or "")
    return make(e) if make is not None else e


def _text(d: dict, key: str) -> str:
    """Строковое поле тела запроса: чужой тип — пустая строка, а не исключение.

    Тело приходит из браузера, но браузер не единственный клиент: число или объект
    на месте строки не должны ронять роут 500-й (tests/test_r8_ic_api.py).
    """
    v = d.get(key)
    return v.strip().strip('"') if isinstance(v, str) else ""


def _int(d: dict, key: str, default: int) -> int:
    """Целочисленное поле тела запроса; мусор — дефолт (как start в прослушивании)."""
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    try:
        return int(v)               # inf/NaN в JSON не бывает, а в теле — бывает
    except (OverflowError, ValueError):
        return default


def _sec(d: dict, key: str) -> float:
    """Секунды из тела запроса; мусор и отрицательное — ноль (с начала клипа)."""
    try:
        return max(0.0, float(d.get(key) or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _float(d: dict, key: str, default: float = 0.0) -> float:
    """Вещественное число из тела запроса."""
    try:
        v = d.get(key)
        return default if v is None else float(v)
    except (TypeError, ValueError):
        return default


# Пределы громкости живого хоста. Верх — с запасом (в стиле `voice_db` ограничен +12),
# низ — не «полная тишина» (−100 в рендере), а граница разумного: тише −60 дБ голос
# не слышно, и выкручивать дальше нечем. Выход за предел — ошибка, а не молчаливая
# подмена: хост понял бы кривое число как есть.
LIVE_DB_MIN = -60.0
LIVE_DB_MAX = 24.0


def _db(d: dict) -> float:
    """Поле `db` команды `gain`: конечное число в разумных пределах.

    Мусор (строка, `None`, `NaN`/`inf` — такие числа бывают в теле, хоть в JSON их
    и нет) и выход за пределы поднимают ошибку: команда громкости с непонятным
    числом не должна уезжать хосту вообще — там её разберёт чужой процесс.
    """
    v = d.get("db")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ReelsiError(umsg("voicefx_no_window",
                               f"Громкость живого звука — не число: {v!r}"))
    try:
        db = float(v)
    except (OverflowError, ValueError):
        raise ReelsiError(umsg("voicefx_no_window",
                               f"Громкость живого звука — не число: {v!r}")) from None
    if not math.isfinite(db):
        raise ReelsiError(umsg("voicefx_no_window",
                               f"Громкость живого звука — не число: {v!r}"))
    if not (LIVE_DB_MIN <= db <= LIVE_DB_MAX):
        raise ReelsiError(umsg("voicefx_no_window",
                               f"Громкость живого звука вне пределов "
                               f"{LIVE_DB_MIN:g}…{LIVE_DB_MAX:g} дБ: {db:.1f}"))
    return db


# ==========================================================================
# Запекание голоса клипа: задание на каждый клип, счёт по одному
# ==========================================================================
# Ход запекания читает превью: пока идёт счёт, играет ИСХОДНЫЙ звук камеры, а на
# кадре видно «голос обрабатывается, k %». Проценты берутся у самого шумодава
# (RoFormer печатает `N/M`), а не у сборки прокси: «прокси 0 %» — это про другое и
# владельцу ничего не говорило.
class _VoiceJob(TypedDict, total=False):
    """Ход запекания ОДНОГО клипа: своё состояние, свой лог, свой PID счёта.

    Задание на клип, а не одно на сервер: раньше слот был один, и открытие второго
    клипа перезаписывало его (трек клипа A мог уехать клипу B, а ход «замирал»).
    Считается по-прежнему по одному — видеокарта одна, — но остальные клипы ждут в
    очереди со своим статусом, а не отбирают чужой.
    """

    xml: str
    src: str
    fx: dict[str, Any]
    queued: bool
    running: bool
    cancelled: bool
    done: bool
    want: bool
    pct: int
    i: int
    n: int
    path: str          # трек в КЕШЕ обработки: имя по настройкам, файл не перезаписывается
    final_wav: str     # голос рядом с XML (версионное имя): его читают AE, DRP, XML, черновик
    key: str
    error: str
    pid: int
    log: list[Any]
    final: bool


# Задания по XML клипа. Живут до перезапуска сервера: записей мало (по одной на
# открытый клип), а ход работы нужен и после возврата в клип.
VOICEJOBS: dict[str, _VoiceJob] = {}
# Что считается ПРЯМО СЕЙЧАС: xml клипа. Замок запекания один — он же и «чья
# очередь»: задача-поток берёт его себе и держит до конца своего счёта.
#
# RLock, а не Lock: состояние заданий читают и под уже взятым замком (журнал,
# тесты, старый `VOICEJOB`), и обычный замок на таком чтении заклинил бы поток
# насмерть — ровно это и случилось с первым заходом.
VOICELOCK = threading.RLock()
VOICERUN = threading.Lock()
VOICECUR = {"xml": ""}
# Последнее ЗАВЕРШЁННОЕ задание: к нему откатывается старый `VOICEJOB`, когда счёт
# уже кончился. Без этого окно, спросившее ход после конца (и старые тесты), видело
# бы пустые «ничего не идёт» вместо честного результата последнего клипа.
VOICELAST = {"xml": ""}
# Хвост лога задания: человеку нужен ход работы, а не весь вывод шумодава на тысячу строк.
VOICE_LOG_KEEP = 40


def _new_slot(xml: str, src: str, fx: dict[str, Any]) -> _VoiceJob:
    """Пустое задание клипа: так его видят и ход, и отмена, и очередь."""
    return {"xml": xml, "src": src, "fx": fx, "queued": False, "running": False,
            "cancelled": False, "done": False, "want": True, "pct": 0, "i": 0, "n": 0,
            "path": "", "final_wav": "", "key": "", "error": "", "pid": 0, "log": [], "final": False}


def _voice_current() -> _VoiceJob | None:
    """Задание, которое считается ПРЯМО СЕЙЧАС, или None. Звать под VOICELOCK."""
    cur = VOICEJOBS.get(str(VOICECUR["xml"]))
    return cur if (cur is not None and cur["running"]) else None


def _voice_front() -> _VoiceJob | None:
    """Первое задание очереди (по порядку заказа): оно пойдёт в счёт. Под VOICELOCK."""
    for j in VOICEJOBS.values():
        if j["queued"] and not j["cancelled"]:
            return j
    return None


class _VoiceJobView(Mapping[str, Any]):
    """`VOICEJOB` — вид на задание: старое имя оставлено для логов и тестов.

    Состояние живёт в `VOICEJOBS` по клипам, а это только читаемая дверь: идёт счёт —
    отдаётся он, кончился — последнее завершённое задание, а нет и такого — пустая
    запись о «ничего не идёт».
    """

    _EMPTY: dict[str, Any] = {"running": False, "done": False, "xml": "", "path": "",
                              "final_wav": "", "key": "", "i": 0, "n": 0, "pct": 0,
                              "error": "", "log": []}

    def _slot(self) -> _VoiceJob | None:
        with VOICELOCK:
            cur = _voice_current()
            if cur is not None:
                return cur
            return VOICEJOBS.get(str(VOICELAST["xml"])) or VOICEJOBS.get(str(VOICECUR["xml"]))

    def __getitem__(self, key: str) -> Any:
        slot = self._slot()
        return self._EMPTY.get(key, "") if slot is None else slot[key]  # type: ignore[literal-required]

    def __iter__(self) -> Any:
        slot = self._slot()
        return iter(slot if slot is not None else self._EMPTY)

    def __len__(self) -> int:
        slot = self._slot()
        return len(slot) if slot is not None else len(self._EMPTY)


VOICEJOB: Mapping[str, Any] = _VoiceJobView()


def _voice_slot(xml: str) -> _VoiceJob:
    """Задание клипа, заводя его при первом обращении (под VOICELOCK)."""
    return VOICEJOBS.setdefault(xml, _new_slot(xml, "", {}))


def _voice_set(slot: _VoiceJob, **fields: Any) -> None:
    """Записать ЧАСТЬ полей задания: своя дверь вместо `TypedDict.update`.

    `update` у TypedDict требует ВСЕ ключи сразу, а задание меняют по частям (ход,
    пути, отмена) — отсюда эта обёртка, одна на все записи состояния.
    """
    slot.update(fields)                              # type: ignore[typeddict-item]


def _voice_emit(xml: str) -> Callable[..., None]:
    """Приём строки хода запекания ИМЕННО этого клипа (в лог его задания)."""
    def _one(line: str = "", /, **vars: Any) -> None:
        with VOICELOCK:
            log: list[Any] = _voice_slot(xml)["log"]
            log.append(log_entry(line, vars))
            del log[:-VOICE_LOG_KEEP]
    return _one


def _voice_progress(xml: str) -> Callable[[int, int], None]:
    """Ход счёта (`i` из `n` кусков) — в проценты задания ЭТОГО клипа."""
    def _one(i: int, n: int) -> None:
        with VOICELOCK:
            _voice_set(_voice_slot(xml), 
                i=int(i), n=int(n), pct=int(round(i * 100 / n)) if n > 0 else 0)
    return _one


def _voice_pid(xml: str) -> Callable[[int], None]:
    """PID дочернего счёта — в задание клипа: «Стоп» гасит его по PID."""
    def _one(pid: int) -> None:
        with VOICELOCK:
            _voice_slot(xml)["pid"] = int(pid)
    return _one


def _voice_take(xml: str) -> bool:
    """Считается ли ЭТОТ клип прямо сейчас (счёт занимает `_voice_reserve`)."""
    with VOICELOCK:
        slot = _voice_slot(xml)
        return bool(slot["running"] and str(VOICECUR["xml"]) == xml)


def _voice_release(xml: str) -> None:
    """Отпустить замок запекания и пустить очередь дальше.

    `VOICERUN` отпускается ДО того, как берётся за следующий клип: он уже наш, и
    неблокирующая попытка занять его же у себя провалилась бы — очередь вставала бы
    навсегда после первого же посчитанного клипа.
    """
    with VOICELOCK:
        _voice_set(_voice_slot(xml), running=False, pid=0)
        VOICELAST["xml"] = xml
        if str(VOICECUR["xml"]) == xml:
            VOICECUR["xml"] = ""
    VOICERUN.release()
    with VOICELOCK:
        slot2 = _voice_next()
        start = _voice_reserve(slot2)
    if start:
        _voice_spawn(slot2["xml"], slot2["src"], slot2["fx"])


def _voice_next() -> _VoiceJob:
    """Первое задание очереди или пустое: под VOICELOCK."""
    return _voice_front() or _new_slot("", "", {})


def _voice_reserve(slot: _VoiceJob) -> bool:
    """Занять замок за клип из очереди. Под VOICELOCK. True — заводить нить счёта.

    Замок берётся НЕБЛОКИРУЮЩЕ: «в очереди» — это состояние клипа, а не поток,
    который спит на замке и о котором никто ничего не знает. Резерв здесь, а не в
    нити, потому что ответ на заказ обязан сказать правду СРАЗУ: между запуском
    нити и её первым шагом есть мгновение, и в нём «считается» ещё не выставлено.
    """
    if not slot["xml"] or not VOICERUN.acquire(blocking=False):
        return False
    _voice_set(slot, running=True, queued=False)
    VOICECUR["xml"] = slot["xml"]
    return True


def _voice_spawn(xml: str, src: str, fx: dict[str, Any]) -> None:
    """Нить счёта клипа: сама работа — в `_voice_run_bake`."""
    threading.Thread(target=_voice_run_bake, args=(xml, src, fx), daemon=True,
                     name="voicefx-bake").start()


def _voice_finish(xml: str, path: str, final_wav: str, key: str) -> None:
    """Запекание клипа кончилось УСПЕШНО: пути — в задание, очередь — дальше."""
    with VOICELOCK:
        _voice_set(_voice_slot(xml), done=True, path=path, final_wav=final_wav, key=key,
                                pct=100, error="")
    _voice_release(xml)


def _voice_fail(xml: str, err: str) -> None:
    """Запекание не вышло: причина — в задание клипа, превью играет звук камеры."""
    with VOICELOCK:
        _voice_set(_voice_slot(xml), done=True, error=err, pct=100)
    _voice_release(xml)


def _voice_stop(xml: str) -> None:
    """Счёт остановлен человеком: пустой ход, без ошибки — это не поломка.

    Отметки ставит ТОЛЬКО отмена (`_voice_cancel`), а здесь — итог: счёт вышел и
    знает про отмену. Замок запекания отпускаем, если он наш: отменённый из очереди
    его и не брал, а `release()` у чужого замка — ошибка.
    """
    with VOICELOCK:
        _voice_set(_voice_slot(xml), done=True, queued=False, running=False, want=False,
                                cancelled=True, error="", pct=0, i=0, n=0)
    if _voice_owns(xml):
        _voice_release(xml)


def _voice_owns(xml: str) -> bool:
    """Замок запекания сейчас за этим клипом? Под VOICELOCK."""
    with VOICELOCK:
        return str(VOICECUR["xml"]) == xml


def _voice_run_bake(xml: str, src: str, fx: dict[str, Any]) -> None:
    """Фоновое запекание ДОРОЖКИ ШУМОДАВА клипа: нить, а не запрос.

    Замок запекания к этому моменту уже занят (`_voice_reserve`: счёт по одному —
    видеокарта одна, а замок её в ядре). Здесь только работа и правда о её исходе:
    ошибка не роняет нить молча, она уезжает в состояние ЭТОГО клипа, и превью
    продолжает играть звук камеры.

    Запекание для превью считает ТОЛЬКО дорожку шумодава (`denoise_track`):
    плагины едут вживую через хост (`core/voicefx_editor --live`), и в запечённый
    трек НЕ запекаются. Полный голос с цепочкой VST печётся только на сборке
    (`ensure_final_voice` / `clip_voice_wav`).
    """
    from core import voicefx
    with VOICELOCK:
        slot = _voice_slot(xml)
        src, fx = slot["src"], slot["fx"]
        is_final = bool(slot.get("final"))
        bad = not slot["want"] or slot["cancelled"]
    if bad:
        _voice_stop(xml)                 # «Стоп» успел раньше, чем нить проснулась
        return
    try:
        norm = voicefx.normalize_fx(fx)
        if is_final:
            final_dst, cache_dst = voicefx.ensure_final_voice(
                xml, src, norm, emit=_voice_emit(xml),
                progress=_voice_progress(xml),
                cancelled=lambda: _voice_cancelled(xml), pid_of=_voice_pid(xml))
            # Плееру — кеш (файл не перезаписывается), `final` — файл рядом с XML для
            # AE/DRP/XML; ровно то же разделение, что в готовой ветке `_voice_bake_json`.
            _voice_finish(xml, cache_dst, final_dst, voicefx.final_voice_key(src, norm))
        else:
            path = voicefx.denoise_track(
                src, norm["denoise"], emit=_voice_emit(xml),
                progress=_voice_progress(xml),
                cancelled=lambda: _voice_cancelled(xml), pid_of=_voice_pid(xml))
            _voice_finish(xml, path, "", voicefx.denoise_cache_path(src, norm["denoise"]))
    except (ReelsiError, SystemExit) as e:
        if _voice_cancelled(xml):
            _voice_stop(xml)
        else:
            _voice_fail(xml, str(e))
    except Exception as e:                       # noqa: BLE001 — голос не повод ронять сервер
        if _voice_cancelled(xml):
            _voice_stop(xml)
        else:
            _voice_fail(xml, f"{type(e).__name__}: {e}")


def _voice_cancelled(xml: str) -> bool:
    """Клип просили остановить? Спрашивается на каждом шаге обработки."""
    with VOICELOCK:
        return bool(_voice_slot(xml)["cancelled"])


def _voice_start(xml: str, src: str, fx: dict[str, Any], final: bool = False) -> bool:
    """Поставить запекание клипа: в счёт или в очередь. False — оно уже идёт у него.

    Настройки и исходник перезаписываются в задании всегда: пока клип стоял в
    очереди, ручки могли поменять — считать надо по последним, а не по тем, с
    которыми его туда поставили. Замок запекания берётся ЗДЕСЬ и неблокирующе: не
    взялся — клип ждёт в очереди, и это его честное состояние, а не спящая нить.
    """
    with VOICELOCK:
        slot = _voice_slot(xml)
        if slot["running"]:
            return False
        if slot["queued"] and slot["src"] == src and slot["fx"] == fx and bool(slot.get("final")) == final:
            return False
        keep_log = slot["done"] or slot["queued"]     # перезаказ после счёта — лог оставляем
        _voice_set(slot, src=src, fx=fx, queued=True, cancelled=False, done=False, want=True,
                   pct=0, i=0, n=0, path="", final_wav="", key="", error="", final=final)
        if not keep_log:
            slot["log"] = []
        started = _voice_reserve(slot)
    if started:
        _voice_spawn(xml, src, fx)
    return True


def _voice_cancel(xml: str) -> int:
    """«Стоп» для клипа: из очереди — долой, у счёта — процесс по PID.

    Возвращает PID снятого счёта (0 — снимали не счёт). Процесс снимается
    `kill_pid`: `Popen` счёта живёт в своём потоке, а способ убийства обязан быть
    один с таймаутом (core.jobstate). Ждать конца потока нечем и не нужно: счёт
    падает исключением отмены, задание получает `done` с пустой ошибкой, а очередь
    идёт дальше — её пускает `_voice_release`.
    """
    with VOICELOCK:
        slot = _voice_slot(xml)
        slot["cancelled"] = True
        slot["want"] = False
        slot["queued"] = False
        pid = int(slot["pid"]) if slot["running"] else 0
    if pid:
        kill_pid(pid, "голос клипа")     # счёт падает отменой и сам отпустит замок
    return pid


def _voice_bake_json(xml: str, src: str, fx: dict[str, Any], final: bool = False) -> Response:
    """Разбор заказа запекания: готов — трек, нет — очередь/счёт и ход работы."""
    if not xml or not os.path.isfile(xml):
        raise ReelsiError(umsg("voicefx_no_xml", f"Нет XML клипа: {xml}", path=xml))
    from core import voicefx
    norm = voicefx.normalize_fx(fx)
    if not voicefx.voice_fx_on(norm):
        voicefx.clear_final_voice(xml)
        with VOICELOCK:
            _voice_set(_voice_slot(xml), done=True, queued=False, running=False, want=False,
                                    path="", final_wav="", key="", i=0, n=0, pct=0, error="")
            VOICELAST["xml"] = xml
        return jsonify(ok=True, xml=xml, ready=False, queued=False, running=False,
                       path="", final="", pct=0, i=0, n=0)
    if not src or not os.path.isfile(src):
        raise ReelsiError(umsg("voicefx_no_src",
                               "Выбери клип — обработка идёт по его звуку"))
    if final:
        final_wav = voicefx.final_voice_path(xml)
        # Плееру — копия в КЕШЕ, а не файл рядом с XML: тот лежит под именем версии
        # настроек и его читают AE/DRP/XML, а браузер играет НЕИЗМЕНЯЕМЫЙ файл кеша
        # (имя по содержимому настроек) — подмена источника звука не рвёт
        # воспроизведение и не зависит от того, кто держит файл рядом с XML.
        cache_wav = voicefx.cache_path(src, norm)
        key = voicefx.final_voice_key(src, norm)
        if voicefx.final_voice_ready(xml, src, norm):
            # Кеш обработки — именно кеш: его чистят, а в свежей рабочей копии его нет
            # вовсе. Итоговый файл рядом с XML — ТА ЖЕ самая запись (её читают AE, DRP,
            # XML), поэтому возвращаем её в кеш и только потом отвечаем «готово»: иначе
            # плеер получил бы тот же ключ с мёртвым URL, `/api/media` ответил бы 404,
            # `<audio>` встал с ошибкой 4, а панель писала бы «голос готов».
            voicefx.restore_cache_from_final(cache_wav, final_wav)
            if os.path.isfile(cache_wav):
                with VOICELOCK:
                    _voice_set(_voice_slot(xml), running=False, queued=False, done=True, want=False,
                               path=cache_wav, final_wav=final_wav, pct=100, key=key, error="")
                    VOICELAST["xml"] = xml
                return jsonify(ok=True, xml=xml, ready=True, queued=False, running=False,
                               path=cache_wav, final=final_wav, pct=100, i=0, n=0, key=key)
            # Копия не удалась (кеш занят или каталог недоступен): «готово» было бы
            # ложью с мёртвой ссылкой — считаем заново, как при неготовом голосе.
        _voice_start(xml, src, norm, final=True)
    else:
        # Превью играет ДОРОЖКУ ШУМОДАВА: плагины — вживую через хост. Готовность
        # проверяем по файлу дорожки (`denoise_cache_path`), а не по итоговому голосу.
        dn_path = voicefx.denoise_cache_path(src, norm["denoise"])
        if os.path.isfile(dn_path):
            with VOICELOCK:
                _voice_set(_voice_slot(xml), running=False, queued=False, done=True, want=False,
                           path=dn_path, final_wav="", pct=100,
                           key=dn_path, error="")
                VOICELAST["xml"] = xml
            return jsonify(ok=True, xml=xml, ready=True, queued=False, running=False,
                           path=dn_path, final="", pct=100, i=0, n=0,
                           key=dn_path)
        _voice_start(xml, src, norm, final=False)
    with VOICELOCK:
        slot = _voice_slot(xml)
        # Ход отдаётся из СВОЕГО задания: у очереди он нулевой, и это честно —
        # «в очереди» не значит «0 % посчитано», а значит «твой счёт ещё не начат».
        job = jsonify(ok=True, xml=xml, ready=False, queued=bool(slot["queued"]),
                      running=bool(slot["running"]), path=slot["path"], final=slot["final_wav"],
                      pct=int(slot["pct"]), i=int(slot["i"]), n=int(slot["n"]),
                      key=str(slot["key"]), error=str(slot["error"]))
    return job


@bp.route("/api/voicefx_bake", methods=["POST"])
def api_voicefx_bake() -> Response:
    """Голос клипа для превью: готов — путь, нет — запустить запекание и отдать ход.

    body: {xml, src, fx, final}. `xml` — XML клипа (рядом с ним ляжет запечённый
    голос версии этих настроек), `src` — файл камеры 1, `fx` — настройки обработки
    с панели голоса.
    `final=True` — для шагов 2–3 (проверка/запекание итогового голоса клипа).
    `path` — то, что играет плеер: НЕИЗМЕНЯЕМАЯ копия в кеше обработки;
    `final` — тот же голос рядом с XML, его читают AE, DRP, Premiere XML и черновик.

    Обработка выключена — запечённый голос клипа УБИРАЕТСЯ (core.voicefx.clear_final_voice):
    «выключил шумодав — звук исходный» обязано работать и для превью, и для XML,
    `.drp` и черновика, которые читают этот файл. Кеш обработки при этом цел.

    Общая форма прогресса для голоса больше не открывается: ход видно строкой
    поверх кадра превью, там же, где идёт сборка прокси (`#pvstage`, блок `.pvpx`).
    """
    d = request.get_json(silent=True) or {}
    xml = _text(d, "xml")
    src = _text(d, "src")
    fx = d.get("fx")
    final = bool(d.get("final"))
    try:
        return _voice_bake_json(xml, src, fx if isinstance(fx, dict) else {}, final=final)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/voicefx_bake_status")
def api_voicefx_bake_status() -> Response:
    """Ход запекания ЭТОГО клипа: {xml, queued, running, done, pct, i, n, path, error, log}.

    Спрошен без `xml=...` — отдаётся задание в работе (у него и спрашивают ход
    сразу после заказа). Неизвестный клип — не ошибка, а пустой ход: окно могло
    открыться на клипе, чьё запекание ещё не заказывали.
    """
    xml = _text(request.args, "xml")
    with VOICELOCK:
        if xml:
            slot = VOICEJOBS.get(xml)
        else:
            slot = _voice_current()          # для старого клиента: ход текущего счёта
        if slot is None:
            return jsonify(ok=True, xml=xml, queued=False, running=False, done=False,
                           pct=0, i=0, n=0, path="", final="", key="", error="",
                           cancelled=False, log=[])
        return jsonify(ok=True, **{k: v for k, v in slot.items() if k != "log"},
                       log=slot["log"][-VOICE_LOG_KEEP:])


@bp.route("/api/voicefx_bake_cancel", methods=["POST"])
def api_voicefx_bake_cancel() -> Response:
    """Отмена запекания голоса клипа: снять из очереди или погасить текущий счёт.

    body: {xml}. Свой клип уходит из очереди, чужой счёт не трогается; текущий
    счёт ЭТОГО клипа снимается по PID дочернего процесса, который мы же и
    запустили (никаких процессов «по имени»). Недописанного файла после отмены не
    остаётся: трек пишется во временную папку и на месте кеша появляется атомарно,
    сутки к нему никто не прикасается (кроме этой же отмены).
    """
    d = request.get_json(silent=True) or {}
    xml = _text(d, "xml")
    try:
        if not xml:
            raise ReelsiError(umsg("voicefx_no_xml", "Нет XML клипа: пусто"))
        pid = _voice_cancel(xml)
        return jsonify(ok=True, xml=xml, cancelled=True, pid=pid)
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/voicefx_devices")
def api_voicefx_devices() -> Response:
    """Устройства вывода звука для живого прослушивания: {ok, devices, default, reason}.

    Отдельным роутом, а не полем соседнего ответа: список нужен ВСЕГДА при открытии
    блока настроек голоса, а окно плагина открывается редко и блокирует запрос до
    своего закрытия — узнавать устройства из него значило бы ждать этого закрытия.

    Без pedalboard роут отдаёт список ТЕМ ЖЕ ответом, а не ошибкой про плагины:
    устройства вывода — это звуковая система (core.voicefx.system_output_devices), и
    с VST3 у них одна судьба только в старом коде. Причина, по которой список пуст
    или неполон, едет полем `reason` — панель показывает её человеку; `error` тут
    остаётся за настоящими сбоями звуковой системы, а не за отсутствием плагинов.
    """
    try:
        try:
            from core import voicefx
            d = voicefx.output_devices()
            # Причина спрашивается ОТДЕЛЬНОЙ дверью ядра: контракт `output_devices`
            # — `{devices, default}`, и поле в нём меняло бы его для всех читателей.
            return jsonify(ok=True, devices=d["devices"], default=d["default"],
                           reason=voicefx.devices_reason())
        except ReelsiError as e:
            raise _forward(e) from None
        except Exception as e:
            raise ReelsiError(umsg("voicefx_devices_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/voicefx_vst_list")
def api_voicefx_vst_list() -> Response:
    """Найденные VST3 для выпадающего списка: {path, name, title}.

    Оболочки (Waves WaveShell) раскрыты по именам: грузить надо имя внутри файла,
    иначе pedalboard возьмёт первое попавшееся.

    `?refresh=1` (кнопка «Обновить список») сбрасывает кеш В ПАМЯТИ: список
    собирается заново по файлам. Сам плагин при этом грузит дочерний процесс, и
    только те файлы, чьи mtime с размером изменились (core/voicefx_scan) —
    обновление списка не переоткрывает всё, что уже прочитано.
    """
    refresh = str(request.args.get("refresh") or "").lower() in ("1", "true", "yes")
    try:
        try:
            from core import voicefx
            return jsonify(ok=True, plugins=voicefx.list_vst3(refresh=refresh))
        except ReelsiError as e:
            raise _forward(e) from None
        except Exception as e:
            raise ReelsiError(umsg("voicefx_vst_list_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/voicefx_edit", methods=["POST"])
def api_voicefx_edit() -> Response:
    """Открыть окно плагина и вернуть сессию СРАЗУ — не дожидаясь его закрытия.

    Запрос больше НЕ висит, пока окно открыто, и это не мелочь: раньше он висел до
    закрытия, а до этого ещё считал шумодав фрагмента клипа — отсюда и жалоба
    «открывается долго». Теперь окно показывает отдельный процесс, поверх всех
    ставит его фоновый поток ядра (`core/voicefx_win`), трек клипа считается там же
    фоном, а состояние плагина по закрытию окна уезжает в профиль спикера САМО
    (`_save_live`). Фронт опрашивает `/api/voicefx_live` и по `done` забирает
    состояние и надпись «сохранено».

    `src` включает ЖИВОЕ прослушивание: пока окно открыто, голос ВСЕГО клипа играет
    через всю цепочку в реальном времени, а позицию задаёт превью (`/api/voicefx_live`
    — play/seek/pause). Без `src` окно открывается без звука, как раньше.

    `speaker` — ключ профиля спикера клипа: по нему состояние плагина записывается
    в профиль. Пусто — записывать некуда, окно всё равно открывается.
    """
    d = request.get_json(silent=True) or {}
    path = _text(d, "path")
    name = _text(d, "name")
    fx = d.get("fx")
    try:
        if not path:
            raise ReelsiError(umsg("voicefx_no_plugin", "Не указан плагин"))
        try:
            from core import voicefx
            session = voicefx.edit_plugin_live(
                path, name, src=_text(d, "src"),
                start=_sec(d, "start"),
                fx=fx if isinstance(fx, dict) else {},
                index=_int(d, "index", -1),
                device=_text(d, "device"),
                speaker=_text(d, "speaker"),
                on_close=_save_live)
            return jsonify(ok=True, sid=session.sid, running=True, state="",
                           track_ready=False, saved=False)
        except ReelsiError as e:
            raise _forward(e) from None
        except Exception as e:
            raise ReelsiError(umsg("voicefx_edit_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


# --------------------------------------------------------------------------- #
# Состояние окна плагина: автосохранение в профиль и живые команды звука
# --------------------------------------------------------------------------- #
def _save_live(session: Any) -> None:
    """Сохранить состояние плагина по закрытию окна — САМО, без кнопки.

    Тонкая обёртка: забрать у сессии то, что уже собрано (состояние и код
    выхода), и отдать записи. Разделено нарочно — `_save_live_done` проверяется
    тестом без процесса окна, а сюда приходит живая сессия ядра.
    """
    _save_live_done(session, session.state, session.exit_code)


def _save_live_dump(session: Any, states: list[dict[str, str]]) -> None:
    """Сохранить состояния ВСЕХ плагинов хоста — по их отдаче (`dump_states`).

    Зовётся хуком гашения (`core.voicefx.live_stop`): хост снимают и при открытом
    окне (закрыли превью, сменили клип, ушли с шага), а состояние знает только его
    процесс. Путь тот же, что у закрытия окна (`_save_live_states`): профиль читается
    свежим, правится ОДНО поле `voice_fx`.
    """
    _save_live_states(session, states, session.exit_code)


def _save_live_done(session: Any, state_b64: str, code: int | None) -> None:
    """Записать состояние плагина в профиль спикера клипа (окно закрылось).

    Тот же путь, что у прежней кнопки «Сохранить у спикера», и это нарочно: профиль
    читается СВЕЖИМ с диска, правится в нём ОДНО поле `voice_fx`, остальные поля
    (пороги, LUT, рамка, папки) уезжают теми же, что лежали. Иначе автозапись
    затирала бы правки соседних панелей.

    Плагин, чьё окно открыли, ищется ПО ПУТИ из цепочки сессии: цепочку могли
    править, пока окно было открыто, и записать состояние одного плагина другому
    (по месту в списке) — это тихо испортить чужую настройку.

    Голос клипа пересчитывается тем же следствием: запечённый `<стем>.voice.<key8>.wav`
    собран под ПРЕЖНИЕ настройки, и следующий заход превью (или сборка) видит
    расхождение ключа кеша и печёт трек заново (`ensure_final_voice`).
    """
    want = _session_open_path(session)
    if code not in (0, None) or not state_b64:
        session.error = str(umsg("voicefx_no_state",
                                 "Плагин закрылся, не отдав состояние"))
        _live_note("окно плагина: состояние не сохранено — {err}", err=session.error)
        return
    if not want:
        return
    if not _save_live_states(session, [{"path": want, "state_b64": state_b64}], code):
        session.error = str(umsg("voicefx_plugin_gone",
                                 "Плагин убран из цепочки — состояние не сохранено",
                                 n=os.path.basename(want)))
        _live_note("окно плагина: состояние не сохранено — плагин убран из цепочки")


def _session_open_path(session: Any) -> str:
    """Путь плагина, чьё окно открывали: сперва прямое поле, иначе — по индексу.

    У сессии есть `open_path` (его ставит `live_open_editor`), но старые записи и
    тесты создают сессию одним индексом — поэтому запасной путь по `chain` остаётся.
    """
    path = str(getattr(session, "open_path", "") or "")
    if path:
        return path
    chain = session.chain
    if 0 <= session.index < len(chain):
        return str(chain[session.index].get("path") or "")
    return ""


def _live_note(line: str, /, **vars: Any) -> None:
    """Строка о живом звуке — в лог сервера.

    Лог идёт в консоль сервера, а НЕ в задание запекания: у запекания своё задание
    на клип (VOICEJOBS), и сбой сохранения плагина не повод объявлять упавшим чужой
    счёт голоса. Фронту причину несёт сессия окна (`session.error`, её отдаёт
    /api/voicefx_live).
    """
    from core.voicefx import console_emit
    console_emit(line, **vars)


def _save_live_states(session: Any, states: list[dict[str, str]],
                      code: int | None) -> bool:
    """Вписать состояния плагинов в профиль спикера: по ПУТИ, одним сохранением.

    Одна дверь на оба случая — закрытие окна (одно состояние) и гашение хоста
    (`dump_states`, все сразу): профиль читается СВЕЖИМ с диска, правится в нём
    ОДНО поле `voice_fx`, остальное уезжает тем, чем лежало. Вторая копия
    «как сохранить профиль» разъехалась бы — и правки соседних панелей
    (пороги, LUT, рамка, папки) затирались бы.

    Плагин ищется ПО ПУТИ, а не по месту в списке: цепочку могли править, пока окно
    было открыто, и записать состояние одного плагина другому — это тихо испортить
    чужую настройку. Плагина в профиле нет — запись по нему не делаем вовсе (в
    отличие от закрытия окна: это не сбой, а цепочка, которую уже почистили).

    Возвращает True, если профиль записан.
    """
    from core import speakers as spk

    if code not in (0, None) or not session.speaker or not states:
        return False
    try:
        profiles = spk.all_speakers()
        spk_key = session.speaker if session.speaker in profiles else ""
        if not spk_key:
            for k, other in profiles.items():
                if other.get("label") == session.speaker:
                    spk_key = k
                    break
        found = profiles.get(spk_key)
        if not isinstance(found, dict):
            session.error = str(umsg("voicefx_no_speaker", "Профиль спикера не найден",
                                     n=session.speaker))
            _live_note("окно плагина: состояние не сохранено — профиль спикера не найден")
            return False
        from core import voicefx
        fx = voicefx.normalize_fx(found.get("voice_fx"))
        vst = [dict(p) for p in fx["vst"]]
        # Пустая запись не трогает профиль: у плагина без состояния (окна не
        # открывали) сохранять нечего, а пустая строка затёрла бы живые ручки.
        applied = 0
        for st in states:
            path = str(st.get("path") or "")
            state = str(st.get("state_b64") or "")
            if not path or not state:
                continue
            target = next((i for i, p in enumerate(vst) if p.get("path") == path), -1)
            if target < 0:
                continue
            vst[target]["state"] = state
            applied += 1
        if not applied:
            return False
        data = dict(found)
        data["voice_fx"] = dict(fx, vst=vst)
        spk.save(spk_key or session.speaker, data)
        session.saved = True
        _live_note("окно плагина: состояние сохранено в профиль спикера «{n}»",
                   n=spk_key or session.speaker)
        return True
    except Exception as e:                       # noqa: BLE001 — сохранение не повод падать
        session.error = f"{type(e).__name__}: {e}"
        _live_note("окно плагина: состояние не сохранено — {err}", err=session.error)
        return False


def _live_payload(session: Any) -> dict[str, Any]:
    """Ответ о сессии: жив ли хост, готов ли трек, что с окном и что пропущено."""
    pid = 0
    try:
        pid = int(session.sid)
    except (TypeError, ValueError):
        pid = 0
    return {
        "sid": session.sid,
        "pid": pid,
        "running": not session.done,
        "done": session.done,
        "saved": session.saved,
        "track_ready": session.track_ready,
        "state": session.state,
        "error": session.error,
        "opened": round(max(0.0, session.t0 and session.opened_at - session.t0), 3),
        # Окно и пропущенные плагины — из файла событий хоста (core.voicefx.live_events):
        # хост живёт часами без окна, и по одному этому ответу панель «Голос» и превью
        # понимают, что окно закрылось и какой плагин цепочки не загрузился.
        "window": bool(session.window),
        "headless": bool(session.headless),
        "skipped": [dict(s) for s in session.skipped],
        # Причина «звук хоста не идёт» (событие `audio_error`): по ней страница снимает
        # своё глушение и играет собственную дорожку или звук камеры. Пусто — звук идёт.
        "audio_error": str(getattr(session, "audio_error", "") or ""),
        "track_input": str(getattr(session, "track_input", "") or ""),
        "input": str(getattr(session, "track_input", "") or ""),
    }


# Живой хост клипа: xml → sid сессии. Хост — ОДИН на клип, и по этому словарю
# повторный заход превью не поднимает второй (звук бы пошёл дважды, а устройства
# вывода — одно). Пустой xml (превью шага 3 живёт без сайдкара) — ключ "".
VOICEHOST: dict[str, str] = {}


def _voice_host_get(xml: str) -> Any:
    """Живой хост этого клипа или None: запись сверяется с реестром сессий ядра."""
    from core import voicefx
    with VOICELOCK:
        sid = VOICEHOST.get(xml, "")
    session = voicefx.live_session(sid) if sid else None
    if session is None or session.done or (session.proc and session.proc.poll() is not None):
        if session is not None and not session.done:
            try:
                voicefx.live_stop(session)
            except Exception:
                pass  # сбой остановки старого процесса не должен блокировать очистку сессии
        with VOICELOCK:
            VOICEHOST.pop(xml, None)
        return None
    return session


def _voice_host_put(xml: str, sid: str) -> None:
    """Запомнить, какой процесс играет голос этого клипа."""
    with VOICELOCK:
        VOICEHOST[xml] = sid


@bp.route("/api/voicefx_host", methods=["POST"])
def api_voicefx_host() -> Response:
    """Поднять ЖИВОЙ ХОСТ клипа БЕЗ окна: голос превью звучит через плагины.

    Это дверь «плагины — вживую ВСЕГДА»: пока открыто превью клипа с включёнными
    плагинами, звук идёт через них процессом `core/voicefx_editor --live`, а окно
    («Настроить») лишь показывает панель уже звучащего хоста командой
    `/api/voicefx_host_edit`. Дорожка шумодава считается фоном из ОБЩЕГО кеша
    (`/api/voicefx_bake` кладёт её же), поэтому хост поднимается сразу, а не через
    минуты счёта: пока дорожки нет, играет звук камеры, и превью показывает ход.

    `xml` — ключ клипа: хост на клип ОДИН, и повторный заход превью (перерисовали
    панель, вернулись из другого клипа) второй процесс не поднимает — иначе голос
    звучал бы дважды, а устройства вывода хватило бы одному из них.

    Плагинов в цепочке нет — хост не нужен: превью играет дорожку `<audio>`, и
    вызывающий эту дверь просто не зовёт. Ядро на пустой цепочке без окна завершается
    само (core/voicefx_editor.execute_live), так что поднятый «на всякий случай» хост
    не остаётся висеть.
    """
    d = request.get_json(silent=True) or {}
    xml = _text(d, "xml")
    src = _text(d, "src")
    fx = d.get("fx")
    try:
        if not src or not os.path.isfile(src):
            raise ReelsiError(umsg("voicefx_no_src",
                                   "Выбери клип — обработка идёт по его звуку"))
        from core import voicefx
        voicefx.live_reap()
        session = _voice_host_get(xml)
        fresh = session is None
        if session is None:
            try:
                session = voicefx.live_host(
                    src=src, start=_sec(d, "start"),
                    fx=fx if isinstance(fx, dict) else {},
                    device=_text(d, "device"), speaker=_text(d, "speaker"),
                    on_close=_save_live)
            except ReelsiError as e:
                raise _forward(e) from None
            except Exception as e:
                raise ReelsiError(umsg("voicefx_edit_failed", f"{type(e).__name__}: {e}",
                                       err=f"{type(e).__name__}: {e}"))
            _voice_host_put(xml, session.sid)
        return jsonify(ok=True, fresh=fresh, **_live_payload(session))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/voicefx_host_edit", methods=["POST"])
def api_voicefx_host_edit() -> Response:
    """Открыть окно плагина в УЖЕ ЗВУЧАЩЕМ хосте: панель того же звука.

    Звук не прерывается и процесс не перезапускается: команда `open_editor` уезжает в
    stdin хоста (`core.voicefx.live_open_editor`), окно рисует его главный поток, а
    подъём окна поверх всех делает сервер (`core/voicefx_win`).

    `path` едет вместе с `index`: у плагина, который не загрузился, индексы панели
    (там цепочка целиком) и хоста (там только загруженные) разъезжаются, и без пути
    «Настроить» открыло бы окно СОСЕДА. Не загрузился — внятная ошибка с именем
    плагина, а не пустое окно.
    """
    d = request.get_json(silent=True) or {}
    sid = _text(d, "sid")
    path = _text(d, "path")
    try:
        from core import voicefx
        voicefx.live_reap()
        session = voicefx.live_session(sid)
        if session is None or session.done:
            raise ReelsiError(umsg("voicefx_no_window",
                                   "Живой звук клипа не поднят — открывать нечего"))
        if session.skipped and path:
            # Пропущенный плагин в хосте не загружен, и открывать в нём нечего: говорим
            # причину С ЕГО ИМЕНЕМ («Bertom_DenoiserClassic не загрузился — пропущен»),
            # а не «окно не открылось». Ищем по ПУТИ: имена у оболочек (WaveShell)
            # повторяются, а путь строки панели и записи хоста один и тот же.
            miss = next((s for s in session.skipped
                         if str(s.get("path") or "") == path), None)
            if miss is not None:
                raise ReelsiError(umsg("voicefx_plugin_skipped",
                                       "{n} не загрузился — пропущен ({err})",
                                       n=miss.get("name", ""), err=miss.get("reason", "")))
        index = _int(d, "index", -1)
        if not voicefx.live_open_editor(session, index, path):
            raise ReelsiError(umsg("voicefx_no_window",
                                   "Хост живого звука не принял команду"))
        return jsonify(ok=True, index=index, **_live_payload(session))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/voicefx_host_stop", methods=["POST"])
def api_voicefx_host_stop() -> Response:
    """Погасить живой хост клипа: превью закрыли, клип сменили, плагины выключили.

    Гасим ДЕРЕВО по PID своего же процесса (`core.voicefx.live_stop`): команда `stop`
    глушит звук, но процесс остаётся жив — а «после закрытия превью не остаётся ни
    одного» относится и к зависшему хосту. Чужой PID не трогаем: он наш от старта.

    Перед гашением хост отдаёт состояние ВСЕХ загруженных плагинов (`dump_states`), и
    оно уезжает в профиль спикера (`_save_live_dump`) — окно могло быть открыто, а
    состояние знает только процесс хоста.

    Сессии нет — не ошибка: хост уже ушёл сам (упал, завершился на пустой цепочке),
    и повторная просьба «погаси» обязана быть тихой, а не 500-й на закрытии превью.
    """
    d = request.get_json(silent=True) or {}
    sid = _text(d, "sid")
    xml = _text(d, "xml")
    try:
        from core import voicefx
        session = voicefx.live_session(sid) if sid else _voice_host_get(xml)
        killed = False
        if session is not None and not session.done:
            killed = voicefx.live_stop(session, _save_live_dump)
        if xml:
            with VOICELOCK:
                VOICEHOST.pop(xml, None)
        return jsonify(ok=True, sid=sid, stopped=bool(killed))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/voicefx_live", methods=["GET", "POST"])
def api_voicefx_live() -> Response:
    """Живой хост: `?sid=` читает состояние сессии, без него — шлёт команду.

    Читающая дверь (GET): жив ли хост, готов ли трек, открыто ли окно, что пропущено
    и забрано ли состояние — фронт опрашивает её, пока хост жив, и по `state` берёт
    настройки плагина, а по `done` понимает, что живого звука больше нет.

    Пишущая (POST): `{"sid": «…», "cmd": …}` уезжает в stdin процесса хоста
    (`core/voicefx.live_command`). Команды шлёт превью: пуск, пауза, перемотка и стык
    монтажа — «звук идёт вместе с видео» — и громкость (`gain`): она едет вместе с
    числом `db`, иначе ползунок громкости не влиял бы на голос, звучащий из процесса
    хоста. Правка цепочки (`chain`) и открытие окна (`open_editor`) идут не сырой
    командой, а ядром (`live_chain`, `live_open_editor`): первая обновляет список
    плагинов сессии, по которому потом ищется плагин для сохранения, вторая поднимает
    окно поверх всех.

    Нет такой сессии (хост закрыли) — внятная ошибка `voicefx_no_window`, а не 500
    и не пустой ответ: превью по ней понимает, что живой звук кончился, и снимает
    с себя глушение собственного голоса.
    """
    from core import voicefx
    voicefx.live_reap()
    sid = _text(request.args, "sid") or _text(request.get_json(silent=True) or {}, "sid")
    session = voicefx.live_session(sid)
    if session is None:
        return jsonify(**umsg_err(ReelsiError(
            umsg("voicefx_no_window", "Окно плагина закрыто — живого звука нет"))))
    # Страница о хосте спрашивает — значит, вкладка жива: по этому молчанию хост без
    # окна снимается сам, если вкладку закрыли, не погасив его (core.voicefx.HOST_IDLE_TTL).
    session.last_poll = time.time()
    if request.method == "GET":
        return jsonify(ok=True, **_live_payload(session))
    d = request.get_json(silent=True) or {}
    cmd = _text(d, "cmd")
    if cmd not in ("play", "seek", "pause", "stop", "chain", "open_editor", "gain",
                   "dump_states"):
        return jsonify(**umsg_err(ReelsiError(
            umsg("voicefx_no_window", f"Неизвестная команда живого звука: {cmd!r}"))))
    try:
        if cmd == "gain":
            # Громкость едет ВМЕСТЕ с числом: без него хост получил бы «gain без db» и
            # оставил прежний уровень — ползунок громкости не влиял бы на голос из хоста.
            sent = voicefx.live_command(session, {"cmd": "gain", "db": _db(d)})
        elif cmd == "chain":
            sent = voicefx.live_chain(session, d.get("fx"))
        elif cmd == "dump_states":
            # Состояние всех плагинов хост отдаёт событием `states`: забираем его
            # ожиданием и сразу пишем в профиль спикера. Хост после этого жив —
            # гасит его отдельная дверь (`/api/voicefx_host_stop`), и там состояние
            # берётся тем же путём.
            before = voicefx.live_dump_request(session)
            states = voicefx.live_dump_wait(session, before)
            sent = True
            if states and session.speaker:
                _save_live_dump(session, states)
            return jsonify(ok=True, sent=sent, states=states, **_live_payload(session))
        elif cmd == "open_editor":
            sent = voicefx.live_open_editor(session, _int(d, "index", -1), _text(d, "path"))
        else:
            sent = voicefx.live_command(session, {"cmd": cmd, "at": _sec(d, "at")})
    except ReelsiError as e:
        return jsonify(**umsg_err(_forward(e)))
    return jsonify(ok=True, sent=sent, **_live_payload(session))


@bp.route("/api/voicefx_roformer")
def api_voicefx_roformer() -> Response:
    """Состояние шумодава RoFormer: стоит ли окружение, какие модели есть, ход установки.

    Отдельным роутом, а не полем соседнего ответа: панель голоса спрашивает это при
    каждом открытии (кнопка «Установить RoFormer (~X ГБ)» показывается только тогда,
    когда движка нет), а установка идёт минутами — её ход читают ЭТИМ же запросом.

    Ничего не запускает и в сеть не ходит: только смотрит файлы окружения.
    """
    try:
        try:
            from core import voicefx_sep
            return jsonify(ok=True, **voicefx_sep.status())
        except ReelsiError as e:
            raise _forward(e) from None
        except Exception as e:
            raise ReelsiError(umsg("voicefx_sep_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/voicefx_roformer_install", methods=["POST"])
def api_voicefx_roformer_install() -> Response:
    """Поставить окружение RoFormer и модели (фоном) — кнопка в панели голоса.

    Ответ приходит сразу: установка — это venv, `pip install` и две модели по
    ~0.9 ГБ, то есть минуты. Ход работы панель читает `/api/voicefx_roformer`
    (`job` в ответе). Повторный вызов не поднимает второго установщика — `started`
    равно false, и это не ошибка: человек просто нажал кнопку ещё раз.
    """
    try:
        try:
            from core import voicefx_sep
            return jsonify(ok=True, started=voicefx_sep.start_install(),
                           size=voicefx_sep.size_text())
        except ReelsiError as e:
            raise _forward(e) from None
        except Exception as e:
            raise ReelsiError(umsg("voicefx_sep_install_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))


@bp.route("/api/voicefx_preview", methods=["POST"])
def api_voicefx_preview() -> Response:
    """Прослушивание «было/стало»: два WAV по звуку камеры 1 текущего клипа.

    `src` — файл камеры 1 (его даёт фронт по XML клипа). Пусто — ошибка
    voicefx_no_src: брать «первый файл из папки камер спикера» нельзя, это будет
    звук другого клипа, и человек услышит не то, что услышит нарезка.
    """
    d = request.get_json(silent=True) or {}
    src = jstr(d, "src").strip().strip('"')
    fx = d.get("fx")
    try:
        if not src:
            raise ReelsiError(umsg("voicefx_no_src",
                                   "Выбери клип — прослушивание идёт по его звуку"))
        try:
            start = float(d.get("start") or 0.0)
        except (TypeError, ValueError):
            start = 0.0                      # мусор в поле «с какого места» — играем с начала
        try:
            from core import voicefx
            before, after = voicefx.preview(src, fx if isinstance(fx, dict) else {},
                                            max(0.0, start))
            return jsonify(ok=True, orig=before, processed=after)
        except ReelsiError as e:
            raise _forward(e) from None
        except Exception as e:
            raise ReelsiError(umsg("voicefx_preview_failed", f"{type(e).__name__}: {e}",
                                  err=f"{type(e).__name__}: {e}"))
    except (ReelsiError, SystemExit) as e:
        return jsonify(**umsg_err(e))
