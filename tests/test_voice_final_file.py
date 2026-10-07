# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Итоговый голос клипа: версионный файл вместо замены на месте, один счёт на клип.

Три вещи, каждая из которых ломалась МОЛЧА (владелец: «на шаге 1 голос обработан,
в проекте AE — звук камеры»):

1. трек лежал под ОДНИМ именем `<стем>.voice.wav` и заменялся на месте. Пока файл
   кто-то держал открытым (плеер превью играет именно его, `/api/media` отдаёт его
   же, открытый проект AE/Premiere), замена на Windows падала `PermissionError`:
   новый звук не появлялся, а исключение уходило в лог. Теперь имя версионное
   (`<стем>.voice.<key8>.wav`), имя версии лежит в сайдкаре `.voice.json` полем
   `"file"`, а читатели ходят через резолвер `final_voice_path`;
2. два заказа одного клипа разом (превью шага 3 плюс повторное нажатие) гнали ДВА
   счёта, и второй падал на файле, который держал первый. Замок по клипу делает
   так, что второй ждёт первый и получает готовый трек без счёта;
3. сбой запекания на сборке был виден только строкой в логе: клип уезжал в проект
   со звуком камеры. Теперь он попадает в ИТОГ сборки предупреждением с причиной.

ffmpeg, RoFormer и VST здесь не запускаются: `render_cached` подменён фейком,
который пишет маленький WAV. Запуск: py -3.10 -m pytest tests/test_voice_final_file.py -q
"""
import json
import os
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from core import speakers, voicefx, voicefx_sep  # noqa: E402
from core.project_file import write_project  # noqa: E402
from api import voicefx as apivfx  # noqa: E402

SPEAKER = "Голос"
FX40 = {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 40}, "vst": []}
FX60 = {"denoise": {"on": True, "engine": "deepfilter", "atten_db": 60}, "vst": []}


def _noop(line: str = "", /, **vars: Any) -> None:
    """emit-заглушка: строки лога в этих проверках не читаются."""
    return None


def _wav(path: str, seconds: float = 0.2) -> str:
    """Маленький настоящий WAV: длину и содержимое читают проба и копирование."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00" * int(48000 * seconds))
    return path


def _norm(fx: dict[str, Any]) -> dict[str, Any]:
    """Те же настройки, что уезжают в ключ кеша: ключ считается по нормализованным."""
    return voicefx.normalize_fx(fx)


def _versioned(xml: str, cam1: str, fx: dict[str, Any]) -> str:
    """Ожидаемое ВЕРСИОННОЕ имя итогового голоса под эти настройки."""
    key = voicefx.final_voice_key(cam1, _norm(fx))
    return os.path.splitext(xml)[0] + ".voice." + key[:8] + ".wav"


def _meta_path(xml: str) -> str:
    """Сайдкар итогового голоса рядом с XML."""
    return os.path.splitext(xml)[0] + ".voice.json"


def _voice_files(xml: str) -> list[str]:
    """Все файлы голоса клипа на диске (легаси и версионные)."""
    return [p for p in voicefx._final_voice_files(xml) if os.path.isfile(p)]


@pytest.fixture(autouse=True)
def voice_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Кеш обработки, профили спикеров и замки клипов — свои на каждый тест.

    Замки сбрасываются нарочно: словарь модульный, и оставленный замок чужого
    (уже удалённого) клипа к следующей проверке отношения не имеет.
    """
    monkeypatch.setattr(voicefx, "VOICEFX_DIR", str(tmp_path / "_voicefx"))
    voices = tmp_path / "speakers"
    voices.mkdir()
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(voices))
    monkeypatch.setattr(voicefx, "_FINAL_LOCKS", {})
    # Отпечаток модели входит в ключ кеша, а модель на машине у всех своя: фиксируем.
    monkeypatch.setattr(voicefx_sep, "stamp", lambda engine: "test-stamp")
    return tmp_path


@pytest.fixture()
def clip(tmp_path: Path) -> tuple[str, str]:
    """Клип: XML нарезки, звук камеры 1 и спикер с включённой обработкой голоса.

    Камера лежит в СВОЕЙ папке: так её видит и роут удаления нарезки — соседние файлы
    клипа он трогает, а исходники обязан оставить.
    """
    media = tmp_path / "media"
    media.mkdir()
    cam1 = _wav(str(media / "cam1.wav"))
    xml = tmp_path / "01_clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")
    write_project(os.path.splitext(str(xml))[0] + ".project.json",
                  {"cams": [cam1], "offsets": [0.0], "fps": 60, "keep": [[0.0, 1.0]],
                   "speaker": SPEAKER})
    speakers.save(SPEAKER, {"label": SPEAKER, "voice_fx": FX40})
    return str(xml), cam1


def _baker(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Фейк запекания: кладёт маленький WAV в КЕШ по ключу настроек.

    Подменяется ровно транспорт звука (`render_cached`): ключ кеша, имя версии и
    копия рядом с XML — настоящие, их проверки и касаются. Список возвращается,
    чтобы видеть, сколько раз счёт вообще начинался.
    """
    calls: list[str] = []

    def fake(src: str, fx: dict[str, Any], emit: Callable[..., Any] = _noop,
             progress: Any = None, cancelled: Any = None, pid_of: Any = None) -> str:
        calls.append(voicefx.final_voice_key(src, fx))
        return _wav(voicefx.cache_path(src, fx))

    monkeypatch.setattr(voicefx, "render_cached", fake)
    return calls


def _hold(monkeypatch: pytest.MonkeyPatch, path: str) -> Callable[[], None]:
    """Держать файл ЗАНЯТЫМ чужим читателем так, как это видит Windows.

    Windows: открытый на чтение файл нельзя ни заменить (`os.replace` поверх него),
    ни удалить — `open()` не берёт FILE_SHARE_DELETE. Linux этого не знает вовсе,
    поэтому там те же два вызова отвечают `PermissionError` для ЭТОГО имени: проверка
    остаётся одинаковой и на машине владельца, и в CI. Возвращается «отпустить».
    """
    if os.name == "nt":
        fh = open(path, "rb")
        fh.read(1)

        def release_nt() -> None:
            fh.close()
        return release_nt
    real_replace, real_remove = os.replace, os.remove
    name = os.path.basename(path)

    def replace(src: str, dst: str, *a: Any, **k: Any) -> None:
        if os.path.basename(str(dst)) == name:
            raise PermissionError(13, "Access is denied", str(dst))
        real_replace(src, dst, *a, **k)

    def remove(target: str, *a: Any, **k: Any) -> None:
        if os.path.basename(str(target)) == name:
            raise PermissionError(13, "Access is denied", str(target))
        real_remove(target, *a, **k)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "remove", remove)

    def release_posix() -> None:
        monkeypatch.setattr(os, "replace", real_replace)
        monkeypatch.setattr(os, "remove", real_remove)
    return release_posix


def _flask_client() -> Any:
    """Клиент боевого приложения: роуты проверяются как их зовёт интерфейс."""
    from flask import Flask
    import api as api_pkg
    app = Flask(__name__)
    app.register_blueprint(api_pkg.bp)
    app.config["TESTING"] = True
    return app.test_client()


# --------------------------------------------------------------------------- #
# 1. Версионный файл: занятый старый трек больше ничего не ломает
# --------------------------------------------------------------------------- #
def test_settings_change_writes_a_new_version_while_the_old_one_is_busy(
        clip: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Смена настроек при ЗАНЯТОМ старом файле: новый файл, сайдкар, никаких исключений.

    Ровно этот случай убивал обработку: трек заменялся на месте, а файл держал
    плеер превью (он играет именно его). Замена не проходила, новый голос не
    появлялся — и сборка потом шла со звуком камеры.
    """
    xml, cam1 = clip
    calls = _baker(monkeypatch)
    first = voicefx.ensure_final_voice(xml, cam1, _norm(FX40), emit=_noop)[0]
    assert first == _versioned(xml, cam1, FX40), first
    assert os.path.isfile(first)

    release = _hold(monkeypatch, first)
    try:
        second = voicefx.ensure_final_voice(xml, cam1, _norm(FX60), emit=_noop)[0]
    finally:
        release()

    assert second != first, "новые настройки переписали прежнюю версию"
    assert second == _versioned(xml, cam1, FX60), second
    assert os.path.isfile(second), "новый итоговый голос не появился"

    meta = json.loads(Path(_meta_path(xml)).read_text(encoding="utf-8"))
    assert meta["file"] == os.path.basename(second), "сайдкар не назвал файл версии"
    assert meta["key"] == voicefx.final_voice_key(cam1, _norm(FX60))
    assert voicefx.final_voice_path(xml) == second, "резолвер не отдал версионный файл"
    assert voicefx.final_voice_ready(xml, cam1, _norm(FX60)) is True
    assert voicefx.final_voice_ready(xml, cam1, _norm(FX40)) is False
    assert calls == [voicefx.final_voice_key(cam1, _norm(FX40)),
                     voicefx.final_voice_key(cam1, _norm(FX60))], calls


def test_busy_version_is_removed_when_released(clip: tuple[str, str],
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Занятую версию не убираем силой, а убираем в следующий раз — когда отпустят.

    Копии копятся на каждую смену настроек (час стерео — десятки мегабайт), но
    удаление best-effort: занятый файл у играющего плеера отбирать нельзя, а сбой
    удаления не должен валить запекание.
    """
    xml, cam1 = clip
    _baker(monkeypatch)
    first = voicefx.ensure_final_voice(xml, cam1, _norm(FX40), emit=_noop)[0]
    release = _hold(monkeypatch, first)
    try:
        second = voicefx.ensure_final_voice(xml, cam1, _norm(FX60), emit=_noop)[0]
        assert os.path.isfile(first), "занятый файл удалили — играющий плеер остался без звука"
        assert os.path.isfile(second)
    finally:
        release()

    # Отпустили: тот же заход видит готовый трек и прибирает то, что не смог раньше.
    assert voicefx.ensure_final_voice(xml, cam1, _norm(FX60), emit=_noop)[0] == second
    assert not os.path.isfile(first), "прежняя версия осталась рядом с XML навсегда"
    assert os.path.isfile(second), "приборка убрала текущий голос"


def test_lost_sidecar_does_not_rewrite_the_busy_version_file(
        clip: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Сайдкар потеряли — файл этой же версии не переписывается: его может держать плеер.

    Имя версии — ключ настроек, и содержимое в файле ровно то же (запекание кешируется
    тем же ключом). Замена занятого файла была бы платой ни за что — ровно тем сбоем,
    ради которого имя и стало версионным.
    """
    xml, cam1 = clip
    _baker(monkeypatch)
    first = voicefx.ensure_final_voice(xml, cam1, _norm(FX40), emit=_noop)[0]
    stamp = os.stat(first).st_mtime_ns
    os.remove(_meta_path(xml))                    # сайдкар потеряли, файл остался

    release = _hold(monkeypatch, first)
    try:
        again = voicefx.ensure_final_voice(xml, cam1, _norm(FX40), emit=_noop)[0]
    finally:
        release()

    assert again == first
    assert os.stat(first).st_mtime_ns == stamp, "файл версии переписали, хотя он занят"
    assert os.path.isfile(_meta_path(xml)), "сайдкар не восстановлен"
    assert voicefx.final_voice_ready(xml, cam1, _norm(FX40)) is True


def test_legacy_file_without_the_file_field_is_still_the_final_voice(
        clip: tuple[str, str]) -> None:
    """Легаси: `<стем>.voice.wav` и сайдкар БЕЗ поля `file` — читается как читался.

    Клипы, запечённые до версионных имён, обязаны звучать: ни `"file"`, ни нового
    имени у них нет, и резолвер отдаёт старое — если файл на месте.
    """
    xml, cam1 = clip
    legacy = os.path.splitext(xml)[0] + ".voice.wav"
    _wav(legacy)
    Path(_meta_path(xml)).write_text(
        json.dumps({"key": voicefx.final_voice_key(cam1, _norm(FX40)), "src": cam1}),
        encoding="utf-8")

    assert voicefx.final_voice_path(xml) == legacy
    assert voicefx.final_voice_ready(xml, cam1, _norm(FX40)) is True

    # Сайдкар назвал имя, которого на диске нет: читатель не должен получить мёртвый путь
    Path(_meta_path(xml)).write_text(
        json.dumps({"key": voicefx.final_voice_key(cam1, _norm(FX40)),
                    "src": cam1, "file": "01_clip.voice.deadbeef.wav"}), encoding="utf-8")
    assert voicefx.final_voice_path(xml) == legacy, "сайдкар увёл читателя в несуществующий файл"


# --------------------------------------------------------------------------- #
# 2. Один счёт на клип: два заказа разом — один рендер
# --------------------------------------------------------------------------- #
def test_two_threads_bake_once(clip: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Два потока на одном клипе: `render_cached` вызван ОДИН раз, путь один и тот же.

    Без замка по клипу оба успевали начать счёт (второй — платить видеокартой
    дважды), а на Windows ещё и падал на файле, который держал первый.
    """
    xml, cam1 = clip
    calls: list[int] = []
    started = threading.Event()

    def slow_render(src: str, fx: dict[str, Any], emit: Callable[..., Any] = _noop,
                    progress: Any = None, cancelled: Any = None, pid_of: Any = None) -> str:
        calls.append(1)
        started.set()
        time.sleep(0.3)                    # второй поток обязан встать на замке клипа
        return _wav(voicefx.cache_path(src, fx))

    monkeypatch.setattr(voicefx, "render_cached", slow_render)
    got: list[str] = []

    def run() -> None:
        got.append(voicefx.ensure_final_voice(xml, cam1, _norm(FX40), emit=_noop)[0])

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    assert started.wait(5), "счёт не начался"
    second.start()
    first.join(10)
    second.join(10)

    assert len(calls) == 1, "клип посчитали дважды: %s" % len(calls)
    assert len(got) == 2 and got[0] == got[1], got
    assert os.path.isfile(got[0])
    assert os.path.isfile(_meta_path(xml))


# --------------------------------------------------------------------------- #
# 3. Сбой запекания видит ИТОГ сборки, а не только лог
# --------------------------------------------------------------------------- #
def test_voice_failure_reaches_the_build_result(clip: tuple[str, str], tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """`final_voice_for_build` при сбое → None, и предупреждение доходит до итога сборки.

    Клип при этом СОБРАН (в `results` он остаётся): это не падение, а «проект уехал
    со звуком камеры». Но человек обязан узнать причину из итога сборки — раньше об
    этом говорила одна строка лога, и дефект жил неделями.
    """
    from core import xml2ae
    from core.app_meta import wrap_emit
    from api import build as apibuild
    from api._core import JOB

    xml, cam1 = clip
    out = tmp_path / "out"
    out.mkdir()

    def boom(*a: Any, **k: Any) -> str:
        raise RuntimeError("ffmpeg упал")

    monkeypatch.setattr(voicefx, "render_cached", boom)
    seen_fail: list[str] = []

    def fake_to_ae_full(xml_path: str, jsx_path: str | None = None,
                        emit: Any = _noop, cancel: Any = None, **kw: Any) -> Any:
        # Та же цепочка, что у настоящей сборки: scene_plan оборачивает emit
        # (`wrap_emit`), а тот зовёт резолвер голоса.
        voice = voicefx.final_voice_for_build(xml_path, cam1, emit=wrap_emit(emit))
        seen_fail.append(str(voice))
        Path(str(jsx_path)).write_text("// заглушка сборки", encoding="utf-8")
        return jsx_path, 1, 1

    monkeypatch.setattr(xml2ae, "to_ae_full", fake_to_ae_full)
    JOB.update(log=[], results=[], failed=[], cancel=False, log_base=0)
    apibuild._run_build_job([{"xml_path": xml}], "separate", str(out))

    assert seen_fail == ["None"], seen_fail
    assert JOB["results"], "клип собран — падения тут нет"
    warns = [f for f in JOB["failed"] if f.get("warn")]
    assert len(warns) == 1, JOB["failed"]
    assert warns[0]["name"] == "01_clip"
    assert "не подключён" in warns[0]["reason"], warns[0]
    assert "ffmpeg упал" in warns[0]["reason"], "причина сбоя до итога не доехала: %s" % warns[0]


# --------------------------------------------------------------------------- #
# 4. Превью играет КОПИЮ из кеша, а файл рядом с XML — читателям вывода
# --------------------------------------------------------------------------- #
def test_bake_route_gives_the_player_the_cache_copy(clip: tuple[str, str],
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """`/api/voicefx_bake` с `final:true`: `path` — в кеш, `final` — рядом с XML.

    Плеер держит файл открытым всё время, пока играет: если это файл рядом с XML,
    он же мешает следующей смене настроек, а браузер отдаёт из кеша куски разных
    версий. Поэтому играет копия в кеше (её имя — ключ настроек), а `final`
    остаётся тем путём, которым пользуются AE, DRP, Premiere XML и черновик.
    """
    xml, cam1 = clip
    _baker(monkeypatch)
    final = voicefx.ensure_final_voice(xml, cam1, _norm(FX40), emit=_noop)[0]

    d = _flask_client().post("/api/voicefx_bake",
                             json={"xml": xml, "src": cam1, "fx": FX40, "final": True}).get_json()

    assert d["ok"] is True and d["ready"] is True and d["running"] is False, d
    assert d["path"] == voicefx.cache_path(cam1, _norm(FX40)), \
        "плееру отдан файл рядом с XML: %s" % d["path"]
    assert d["final"] == final, "итоговый голос рядом с XML не назван"
    assert os.path.dirname(d["final"]) == os.path.dirname(xml)
    assert d["path"] != d["final"]


def test_bake_route_restores_a_lost_cache_from_the_final_file(
        clip: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Кеш вычистили, а итоговый файл рядом с XML остался: «готово» отдаётся с ЖИВЫМ путём.

    Кеш обработки — именно кеш: его чистят (и в свежей рабочей копии его нет вовсе),
    а итоговый файл рядом с XML остаётся — на него ссылается собранный проект. Отдав
    плееру прежний ключ кеша без проверки, роут отвечал «голос готов» на мёртвый URL:
    `/api/media` отдавал 404, `<audio>` вставал с ошибкой 4. Функция возврата в кеше
    уже есть (`restore_cache_from_final`), и запись в ней та же самая — байт в байт.
    """
    xml, cam1 = clip
    _baker(monkeypatch)
    final = voicefx.ensure_final_voice(xml, cam1, _norm(FX40), emit=_noop)[0]
    cache = voicefx.cache_path(cam1, _norm(FX40))
    os.remove(cache)                                  # кеш почистили, файл рядом с XML цел
    monkeypatch.setattr(apivfx, "VOICEJOBS", {})
    monkeypatch.setattr(apivfx, "VOICERUN", threading.Lock())
    monkeypatch.setattr(apivfx, "VOICELOCK", threading.RLock())

    d = _flask_client().post("/api/voicefx_bake",
                             json={"xml": xml, "src": cam1, "fx": FX40, "final": True}).get_json()

    assert d["ok"] is True and d["ready"] is True and d["running"] is False, d
    assert d["path"] == cache, "плееру отдан не кеш: %s" % d["path"]
    assert os.path.isfile(d["path"]), "плееру отдан мёртвый путь: %s" % d["path"]
    assert Path(d["path"]).read_bytes() == Path(final).read_bytes(), \
        "вернули в кеш не тот же самый голос"
    assert d["final"] == final
    with apivfx.VOICELOCK:
        slot = apivfx.VOICEJOBS[xml]
    assert slot.get("done") is True and slot.get("path") == cache, slot


def test_bake_route_counts_again_when_the_cache_copy_fails(
        clip: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Копию в кеш сделать не вышло — не «готово» с мёртвой ссылкой, а счёт заново.

    Путь кеша в состоянии задания оставлять нельзя: панель и плеер прочитали бы его
    как готовый голос, а файла нет. Поэтому клип уезжает считать заново — ровно так
    же, как при неготовом голосе.
    """
    xml, cam1 = clip
    _baker(monkeypatch)
    final = voicefx.ensure_final_voice(xml, cam1, _norm(FX40), emit=_noop)[0]
    cache = voicefx.cache_path(cam1, _norm(FX40))
    os.remove(cache)                                  # кеша нет и вернуть его нечем
    assert os.path.isfile(final), "итоговый файл рядом с XML пропал — случай не тот"
    monkeypatch.setattr(apivfx, "VOICEJOBS", {})
    monkeypatch.setattr(apivfx, "VOICERUN", threading.Lock())
    monkeypatch.setattr(apivfx, "VOICELOCK", threading.RLock())
    monkeypatch.setattr(voicefx, "restore_cache_from_final", lambda c, f: False)
    started: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(apivfx, "_voice_start",
                        lambda x, s, f, final=False: started.append((x, s, final)) or True)

    d = _flask_client().post("/api/voicefx_bake",
                             json={"xml": xml, "src": cam1, "fx": FX40, "final": True}).get_json()

    assert d["ok"] is True and d["ready"] is False, d
    assert started == [(xml, cam1, True)], "счёт не запущен: %s" % started
    assert d["path"] == "", "в ответ уехал мёртвый путь кеша: %s" % d["path"]
    assert not os.path.isfile(cache)
    with apivfx.VOICELOCK:
        slot = apivfx.VOICEJOBS.get(xml) or {}
    assert slot.get("path", "") == "", "мёртвый путь кеша остался в состоянии задания"


# --------------------------------------------------------------------------- #
# 5. Уборка: все версии и сайдкар; удаление нарезки видит версии
# --------------------------------------------------------------------------- #
def test_clear_final_voice_removes_every_version(clip: tuple[str, str],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """`clear_final_voice` убирает ВСЕ версии голоса и сайдкар, а не одну из них.

    Версий на диске бывает несколько (имя меняется вместе с настройками), и
    «выключил обработку — звук исходный» выполняется, только если ушли все: любую
    оставшуюся продолжат читать XML, `.drp` и черновик.
    """
    xml, cam1 = clip
    _baker(monkeypatch)
    first = voicefx.ensure_final_voice(xml, cam1, _norm(FX40), emit=_noop)[0]
    second = voicefx.ensure_final_voice(xml, cam1, _norm(FX60), emit=_noop)[0]
    # Первую версию вернём руками: обычно её убирает сам `ensure_final_voice`.
    _wav(first)
    assert len(_voice_files(xml)) == 2

    assert voicefx.clear_final_voice(xml) is True
    assert _voice_files(xml) == [], "версия голоса осталась рядом с XML"
    assert not os.path.isfile(_meta_path(xml)), "сайдкар голоса остался"
    assert voicefx.final_voice_path(xml) == os.path.splitext(xml)[0] + ".voice.wav"
    assert not os.path.isfile(second), "текущая версия пережила выключение обработки"


def test_clip_delete_lists_versioned_voice_files(clip: tuple[str, str],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Сухой прогон удаления нарезки перечисляет и ВЕРСИОННЫЕ файлы голоса.

    Они лежат рядом с XML под тем же стемом, то есть сайдкары клипа: не попади они
    в список — остались бы на диске навсегда при удалении нарезки.
    """
    xml, cam1 = clip
    _baker(monkeypatch)
    first = voicefx.ensure_final_voice(xml, cam1, _norm(FX40), emit=_noop)[0]
    second = voicefx.ensure_final_voice(xml, cam1, _norm(FX60), emit=_noop)[0]
    # Первую версию вернём руками: обычно её убирает сам `ensure_final_voice`.
    _wav(first)

    d = _flask_client().post("/api/clip_delete", json={"xml": xml, "dry": True}).get_json()

    assert d["ok"] is True, d
    listed = {os.path.basename(f["path"]) for f in d["files"]}
    assert os.path.basename(second) in listed, d["files"]
    assert os.path.basename(first) in listed, "прежняя версия голоса не названа в удалении"
    assert os.path.basename(_meta_path(xml)) in listed
    assert os.path.isfile(second), "сухой прогон удалил файл"
    assert cam1 not in {f["path"] for f in d["files"]}, "исходник камеры попал под удаление"
