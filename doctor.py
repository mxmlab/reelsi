# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Проверка окружения: что стоит, что нет и что именно из-за этого не заработает.

    python reelsi/doctor.py

Зачем отдельный скрипт. Reelsi тянет полтора десятка тяжёлых зависимостей, и почти
каждая опциональна: без `rembg` фон у картинок просто не снимается, без `gigaam`
недоступен русский CTC-движок, без `silero-vad` молча выключается детектор вздохов.
Человек на свежем клоне узнаёт об этом в середине джоба, из ошибки, которая называет
не ту причину — на этом уже попадались (тест вздохов, 2026-08-06).

Поэтому здесь два правила:

- **Называть настоящую причину.** Не «детектор вздохов недоступен», а какого именно
  пакета нет и что набрать, чтобы он появился.
- **Отличать «сломано» от «не установлено опциональное».** Красное — то, без чего
  Reelsi не запустится вообще. Жёлтое — то, из-за чего отключится конкретная функция,
  и надо сказать какая.

Выходной код: 0 — можно работать (возможно без части функций), 1 — есть красное.
"""
import importlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import warnings
from typing import Any, cast

HERE = os.path.dirname(os.path.abspath(__file__))
from core import paths
from core.app_meta import APP_VERSION, t, ui_lang
from core.umsg import ReelsiError, cli_error

# Отчёт должен читаться целиком. Flask ругается на чтение __version__, fontTools пишет
# в stderr про кривые таблицы в чужих шрифтах — к состоянию окружения ни то, ни другое
# отношения не имеет, а строк даёт больше, чем сам отчёт.
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Своя точка подмены для тестов. Мокать напрямую shutil.which нельзя: он глобальный,
# и его зовут при импорте другие пакеты — torch на этом ломался с AttributeError,
# а тест сообщал «нет torch» вместо настоящей причины.
_which = shutil.which
logging.getLogger("fontTools").setLevel(logging.CRITICAL)

OK, WARN, ERR = "OK  ", "WARN", "FAIL"
_rows: list[tuple[str, str, str]] = []
_bad = 0

# Версии Python, которые проект сам объявляет рабочими: их проверяет CI
# (`.github/workflows/ci.yml` — сборка на 3.10 и джоба `smoke` на 3.12/3.13/3.14).
# Внутри этого набора предупреждать не о чем: версия уже собирается на каждом
# коммите. Список живёт здесь ОДНИМ местом, а расхождение с ci.yml ловит тест
# (`tests/test_ui_fixes_linux.py`): два списка без сверки разъезжаются молча.
PYTHONS_CHECKED = frozenset({"3.10", "3.12", "3.13", "3.14"})


def row(status: str, what: str, detail: str = "") -> None:
    global _bad
    if status == ERR:
        _bad += 1
    _rows.append((status, what, detail))


# Имя пакета в pip != имя модуля для импорта.
_DIST = {"PIL": "pillow", "sklearn": "scikit-learn", "silero_vad": "silero-vad",
         "faster_whisper": "faster-whisper", "fontTools": "fonttools"}


def _mod(name: str) -> tuple[bool, str]:
    """Модуль импортируется? Возвращает (bool, версия-или-причина).

    Версию берём из метаданных дистрибутива, а не из `__version__`: у Flask этот
    атрибут объявлен устаревшим и его чтение печатает предупреждение прямо в отчёт.
    """
    try:
        importlib.import_module(name)
    except ReelsiError: raise
    # сбой импорта и есть результат проверки: вернём тип ошибки, а не уроним весь отчёт
    except Exception as e:
        return False, type(e).__name__
    try:
        from importlib.metadata import version
        return True, version(_DIST.get(name, name))
    except ReelsiError: raise
    # версия без метаданных (пакет из исходников, без dist-info) — не ошибка, версия просто неизвестна
    except Exception:
        return True, ""


# --------------------------------------------------------------------------- #
# Обязательное: без этого не запустится вообще
# --------------------------------------------------------------------------- #
def check_core() -> None:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v[:2] == (3, 10):
        row(OK, "Python", ver)
    elif v[:2] < (3, 10):
        row(ERR, "Python", f"{ver} — " + t("нужен 3.10, часть синтаксиса не разберётся"))
    elif f"{v.major}.{v.minor}" in PYTHONS_CHECKED:
        # Версия из матрицы CI: проект её проверяет сам, и предупреждение тут
        # выглядело как проблема, которой нет (на 3.12 всё работает).
        row(OK, "Python", f"{ver} — " + t("эта версия в наборе, который проверяет CI "
                                        "(3.10 и 3.12–3.14)"))
    else:
        # Вне проверяемого набора: колёса torch/gigaam под неё бывают не собраны
        row(WARN, "Python", f"{ver} — " + t("проект писался на 3.10; тут возможны колёса, "
                            "которых нет под твою версию"))

    if _which("ffmpeg"):
        try:
            out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True,
                                 timeout=10).stdout.splitlines()
            row(OK, "ffmpeg", (out[0][:60] if out else t("найден")))
        except ReelsiError: raise
        # ffmpeg найден, но не отвечает: строка WARN с типом ошибки, остальные проверки идут дальше
        except Exception as e:
            row(WARN, "ffmpeg", t("найден, но не отвечает: {err}", err=type(e).__name__))
    else:
        row(ERR, "ffmpeg", t("НЕТ в PATH — без него не будет ни звука, ни рендера. "
                           "Windows: winget install Gyan.FFmpeg | mac: brew install ffmpeg"))

    for name, why in (("flask", t("веб-интерфейс")), ("numpy", t("всё")),
                      ("scipy", t("синхронизация камер, VAD")), ("soundfile", t("чтение звука"))):
        ok, info = _mod(name)
        row(OK if ok else ERR, name, info if ok else t("НЕТ — без него не работает: {why}", why=why))


# --------------------------------------------------------------------------- #
# Вычислитель: какое устройство реально возьмётся
# --------------------------------------------------------------------------- #
def check_compute() -> None:
    ok, info = _mod("torch")
    if not ok:
        # Причину печатаем: «не установлен» и «установлен, но падает на импорте»
        # (битые DLL CUDA, конфликт версий) чинятся совершенно по-разному.
        row(ERR, "torch", t("НЕТ ({info}) — не будет ни распознавания, ни ротоскопа. "
                          "Ставить ОТДЕЛЬНО под свою видеокарту, см. README", info=info))
        return
    import torch
    from core.device import pick_device, ct2_device

    dev = pick_device()
    if dev == "cuda":
        try:
            name = torch.cuda.get_device_name(0)
            free, total = torch.cuda.mem_get_info()
            row(OK, t("устройство"), f"cuda — {name}, " + t("свободно "
                                  "{free:.1f} из {total:.1f} ГБ", free=free / 2**30, total=total / 2**30))
        except ReelsiError: raise
        # сведения о видеопамяти необязательны: cuda уже подтверждено, без цифр строка остаётся OK
        except Exception:
            row(OK, t("устройство"), "cuda")
    elif dev == "mps":
        row(WARN, t("устройство"), t("mps (Apple Silicon) — работает, но медленнее CUDA; "
                                "на живом маке не проверялось, см. docs/PLATFORMS.md"))
    else:
        row(WARN, t("устройство"), t("CPU (torch {info}) — GPU не найден. Всё посчитается, "
                                "но в десятки раз медленнее. Если карта есть, значит "
                                "приехала CPU-сборка torch: переставь по README", info=info))

    ct2_dev, ct2_ct = ct2_device()
    if ct2_dev == "cuda":
        row(OK, t("транскрипция"), t("faster-whisper на GPU ({ct2_ct})", ct2_ct=ct2_ct))
    else:
        row(WARN, t("транскрипция"), t("faster-whisper пойдёт на CPU. Это не чинится "
                                  "настройками: CTranslate2 не поддерживает ни Metal, "
                                  "ни ROCm. См. docs/PLATFORMS.md"))


def check_whisper_cpp() -> None:
    # whisper.cpp — опциональная замена faster-whisper для Mac/AMD (Metal/Vulkan).
    # Это не питоновский пакет, поэтому ставится мимо pip; отсутствие бинарника
    # или моделей только отключает быструю транскрипцию (WARN, а не FAIL — как в check_optional).
    try:
        from core import whisper_cpp
        if not whisper_cpp.whisper_cli_path():
            row(WARN, "whisper.cpp", t("бинарника нет — на Mac/AMD быстрой транскрипции "
                                     "не будет. Поставить по запросу: python "
                                     "-m core.whisper_cpp install (или brew/scoop)"))
            return
        models = [size for size in whisper_cpp.MODEL_FILES if whisper_cpp.model_path(size)]
        if models:
            row(OK, "whisper.cpp", t("бинарник найден, модели: {models}", models=", ".join(models)))
        else:
            row(WARN, "whisper.cpp", t("моделей нет — скачаются при первом выборе "
                                      "движка (large-v3 ≈ 3 ГБ)"))
    except ReelsiError: raise
    # сбой проверки whisper виден строкой WARN с типом ошибки; отчёт должен дойти до конца
    except Exception as e:
        row(WARN, "whisper.cpp", t("проверка не удалась: {err}", err=type(e).__name__))


def check_voice_sep() -> None:
    """Шумодав RoFormer: стоит ли своё окружение и какие модели скачаны.

    RoFormer ставится ОТДЕЛЬНО от проекта (`core/voicefx_sep`): venv с
    audio-separator и две модели по ~0.9 ГБ в `~/.reelsi/voice_sep`. Поэтому
    «нет» — это WARN с тем, что именно отключится: без него остаётся прежний
    deep-filter. Проверяем ФАЙЛЫ, а не импорт: audio-separator тянет torch и
    onnxruntime-gpu, и грузить это в диагностику незачем.
    """
    try:
        from core import voicefx_sep
        st = voicefx_sep.status()
        done = [e for e in voicefx_sep.ENGINES if st["engines"][e]["installed"]]
        left = [e for e in voicefx_sep.ENGINES if not st["engines"][e]["installed"]]
        if not left:
            row(OK, "RoFormer", t("окружение {env}, модели: {models}",
                                  env=voicefx_sep.ENV_DIR, models=", ".join(done)))
        elif not st["ready"]:
            row(WARN, "RoFormer", t("нет окружения ({env}) — отключится шумодав "
                                    "RoFormer, останется deep-filter. Качается кнопкой "
                                    "«Скачать RoFormer ({size})» в панели голоса",
                                    env=voicefx_sep.ENV_DIR, size=st["size"]))
        else:
            row(WARN, "RoFormer", t("нет моделей: {models} — качаются кнопкой "
                                    "«Скачать RoFormer ({size})» в панели голоса",
                                    models=", ".join(left), size=st["size"]))
    except ReelsiError: raise
    # сбой проверки RoFormer виден строкой WARN с типом ошибки; отчёт должен дойти до конца
    except Exception as e:
        row(WARN, "RoFormer", t("проверка не удалась: {err}", err=type(e).__name__))


# --------------------------------------------------------------------------- #
# Опциональное: без чего отключается КОНКРЕТНАЯ функция
# --------------------------------------------------------------------------- #
OPTIONAL = [
    ("faster_whisper", "субтитры движком по умолчанию (Whisper)"),
    ("transformers",   "Qwen2.5-Omni, CTC-движки, детектор вздохов"),
    ("silero_vad",     "детектор вздохов и «кхе»"),
    ("gigaam",         "русский CTC-движок GigaAM (пословная нарезка)"),
    ("librosa",        "поиск повторов (SSM)"),
    ("rembg",          "снятие фона у сгенерированных картинок"),
    ("PIL",            "работа с картинками-вставками"),
    ("fontTools",      "список установленных шрифтов в UI"),
    ("anthropic",      "профиль провайдера Claude"),
    ("questionary",    "интерактивный CLI"),
    ("sklearn",        "переобучение детектора вздохов (tools/train_breath.py)"),
    # pedalboard не объявлялся НИГДЕ — ни в requirements, ни здесь, — и человек
    # узнавал о нехватке только в момент включения: «Нет пакета pedalboard».
    # Строка жёлтая: без пакета панель голоса работает, живого прослушивания нет.
    ("pedalboard",     "живое прослушивание через плагины VST3 и список устройств вывода"),
]


def check_optional() -> None:
    for name, feature in OPTIONAL:
        ok, info = _mod(name)
        # На отказе печатаем И тип исключения: `_mod` возвращает вместо версии имя
        # типа, и именно оно отличает «пакета нет» от «пакет стоит, но падает на
        # импорте» (rembg с неработающим numba давал «rembg отсутствует», человек
        # шёл ставить то, что уже стоит).
        row(OK if ok else WARN, name,
            info if ok else t("нет ({info}) — отключится: {feature}",
                              info=info, feature=t(feature)))


# --------------------------------------------------------------------------- #
# Данные и шрифт
# --------------------------------------------------------------------------- #
def check_assets() -> None:
    for f, why in (("refblobs.json", t("шаблоны субтитр-графики")),
                   ("sub_template.xml", t("шаблон субтитров")),
                   ("refblobs_color.json", t("цветные блобы субтитров"))):
        p = paths.data(f)
        row(OK if os.path.exists(p) else ERR, f,
            t("на месте") if os.path.exists(p) else t("НЕТ — без него не собрать: {why}", why=why))

    # Шрифт субтитров: зашит в refblobs.json. Не найдётся — Premiere молча подставит
    # свой, и вёрстка строки поедет. Это не ошибка запуска, но узнать лучше заранее.
    want = "SFPro-CondensedSemibold"
    try:
        from core import fonts
        # fontTools ругается на кривые таблицы в чужих шрифтах прямо в stderr, мимо
        # logging: на тысяче установленных шрифтов этих строк больше, чем самого отчёта.
        with open(os.devnull, "w") as devnull:
            real, sys.stderr = sys.stderr, devnull
            try:
                have = {f["ps"] for f in fonts.list_fonts()}
            finally:
                sys.stderr = real
        if want in have:
            row(OK, t("шрифт субтитров"), want)
        elif any(p.startswith("SFPro") for p in have):
            # Семья стоит, а точного начертания среди PostScript-имён нет. Это ещё не
            # беда: SF Pro бывает вариативным, и тогда ширина — ось внутри одного
            # файла, отдельного «Condensed» в списке не будет, а Premiere его найдёт.
            row(WARN, t("шрифт субтитров"),
                t("SF Pro стоит, но {want} отдельным начертанием не виден. Если субтитры "
                "собираются нормально — это вариативный шрифт и всё в порядке; если "
                "вёрстка строки едет, поставь Condensed или пересобери блобы "
                "(tools/harvest_good.py)", want=want))
        else:
            row(WARN, t("шрифт субтитров"),
                t("{want} не найден и SF Pro не установлен — Premiere подставит свой "
                "шрифт, и строка субтитров поедет. Поставь SF Pro или пересобери блобы "
                "под свой: tools/harvest_good.py", want=want))
    except ReelsiError: raise
    # сбой проверки шрифта виден строкой WARN; остальные проверки продолжают идти
    except Exception as e:
        row(WARN, t("шрифт субтитров"), t("не смог проверить ({err})", err=type(e).__name__))

    ai = paths.root("ai_config.json")
    row(OK if os.path.exists(ai) else WARN, "ai_config.json",
        t("есть (ключи провайдеров)") if os.path.exists(ai)
        else t("нет — ИИ-шаги будут недоступны, пока не заведёшь профиль (⚙ в интерфейсе)"))

    # Каталог возможностей моделей (models.dev): по нему решается, что
    # модель умеет (reasoning/temperature/лимиты). Кэш на диск рядом с ai_config.
    try:
        from core.aicut import catalog
        import json as _json
        p = catalog.catalog_cache_path()
        if os.path.isfile(p):
            try:
                data = _json.load(open(p, encoding="utf-8"))
                nprov = len(data)
                when = time.strftime("%d.%m %H:%M",
                                     time.localtime(os.path.getmtime(p)))
                row(OK, t("каталог models.dev"),
                    t("кэш есть ({when}, {nprov} провайдеров) — возможности моделей известны",
                      when=when, nprov=nprov))
            except ReelsiError: raise
            # битый кэш каталога виден строкой WARN и перезапишется сам; падать из-за него нельзя
            except Exception:
                row(WARN, t("каталог models.dev"), t("битый кэш, перезапишется при первом обращении"))
        else:
            row(WARN, t("каталог models.dev"),
                t("кэша нет — при первом ИИ-вызове каталог подтянется; офлайн возможности моделей неизвестны"))
    except ReelsiError: raise
    except Exception:
        pass                                   # каталог не критичен, doctor не падает


# --------------------------------------------------------------------------- #
# Куда пишем и откуда берём материал
# --------------------------------------------------------------------------- #
def check_workspace() -> None:
    try:
        from core.app_meta import env, out_dir
        from core.cams import find_cam_dirs
        import reelsi as cli
        base = env("BASE") or cli.DEFAULT_BASE
    except ReelsiError: raise
    # рабочая папка не определилась: строка WARN и выход из этой проверки, отчёт продолжается
    except Exception as e:
        row(WARN, t("рабочая папка"), t("не смог определить ({err})", err=type(e).__name__))
        return
    if not os.path.isdir(base):
        row(ERR, t("рабочая папка"), t("{base} — не существует. Задай REELSI_BASE или --base", base=base))
        return
    cams = [os.path.basename(p) for p in find_cam_dirs(base)]
    row(OK if cams else WARN, t("рабочая папка"),
        t("{base} — камеры: {cams}", base=base, cams=", ".join(cams)) if cams
        else t("{base} — папок «камера…» нет, класть материал сюда", base=base))
    row(OK, t("папка результата"), out_dir(base))


def check_ae_tooling() -> None:
    """node нужен только core/verify_jsx.py — проверить .jsx до открытия в AE."""
    if _which("node"):
        row(OK, "node", t("есть — core/verify_jsx.py сможет проверять синтаксис .jsx"))
    else:
        row(WARN, "node", t("нет — core/verify_jsx.py не проверит синтаксис .jsx "
                          "(остальные его проверки работают)"))


def check_external() -> None:
    """Внешние опциональные бинарники: rclone (гугл-диск) и After Effects (рендер).

    То же правило, что у пакетов в check_optional: нет — код 0 и сказано, какая
    функция отключится; есть, но падает — код 1. Поиск AE не дублируем: зовём
    тот же find_ae() из core/aerender.py, чтобы у рендера и у doctor был один
    источник правды о том, где стоит After Effects (движок живёт в `core/` — из
    `api/` его брать значило бы тащить в диагностику Flask-слой)."""
    if _which("rclone"):
        try:
            out = subprocess.run(["rclone", "version"], capture_output=True, timeout=10,
                                 text=True)
            ok = out.returncode == 0
            reason = t("код {code}", code=out.returncode)
        except ReelsiError: raise
        # запуск проверки упал: ok=False и тип ошибки уходят в строку отчёта
        except Exception as e:
            ok = False
            reason = type(e).__name__
        if ok:
            row(OK, "rclone", t("есть — скачивание материала с гугл-диска работает"))
        else:
            row(ERR, "rclone", t("в PATH есть, но не запускается ({reason}) — отключится "
                               "скачивание материала с гугл-диска", reason=reason))
    else:
        row(WARN, "rclone", t("нет в PATH — отключится скачивание материала с гугл-диска. "
                            "Windows: winget install Rclone.Rclone | mac: brew install rclone"))

    if os.name != "nt":
        row(WARN, "After Effects", t("безголовый рендер доступен только на Windows"))
        return
    try:
        from core.aerender import find_ae
    # импорт core.aerender упал: строка WARN, сама диагностика продолжается
    except Exception as e:
        row(WARN, "After Effects", t("не смог проверить ({err})", err=type(e).__name__))
        return
    try:
        ae = find_ae()
    except ReelsiError: raise
    # find_ae упал: строка ERR с типом ошибки — это и есть ответ «безголовый рендер недоступен»
    except Exception as e:
        row(ERR, "After Effects", t("есть, но падает ({err}) — отключится "
                                  "безголовый рендер", err=type(e).__name__))
        return
    if ae:
        row(OK, "After Effects", t("{ver} — безголовый рендер доступен", ver=ae[2]))
    else:
        row(WARN, "After Effects", t("не найден — отключится безголовый рендер. "
                                   "Установи After Effects (искал в "
                                   "%ProgramFiles%\\Adobe\\Adobe After Effects *)"))


def _plur(n: int, one: str, few: str, many: str, en_one: str = "", en_other: str = "") -> str:
    """«1 функция» / «2 функции» / «5 функций» — иначе отчёт выглядит машинным."""
    if ui_lang() == "en" and en_one:
        return en_one if abs(n) == 1 else en_other
    n = abs(n) % 100
    d = n % 10
    if 10 < n < 20:
        return many
    return one if d == 1 else few if 1 < d < 5 else many


def _flush(title: str | None = None) -> None:
    """Напечатать накопленные строки. Заголовки печатаются между блоками, а не
    все скопом перед таблицей — иначе секция «опциональное» уезжает наверх."""
    global _rows
    if not _rows:
        return
    if title:
        print(f"\n--- {title} ---")
    width = max(len(w) for _, w, _ in _rows) + 2
    for status, what, detail in _rows:
        print(f"[{status}] {what:<{width}} {detail}")
    _rows = []


def main() -> int:
    global _bad, _rows
    _bad = 0
    _rows = []
    paths.require_source_tree()
    print(t("Reelsi v{ver} — проверка окружения ({sys} {rel}, {arch})",
            ver=APP_VERSION, sys=platform.system(), rel=platform.release(),
            arch=platform.machine()))
    check_core()
    check_compute()
    check_whisper_cpp()
    check_voice_sep()
    check_assets()
    check_workspace()
    check_ae_tooling()
    check_external()
    warns = sum(1 for s, _, _ in _rows if s == WARN)
    _flush()
    check_optional()
    warns += sum(1 for s, _, _ in _rows if s == WARN)
    _flush(t("опциональное (без чего отключается конкретная функция)"))

    print()
    if _bad:
        print(t("НЕ ГОТОВО: {n} {w} — пока не починишь, не запустится.",
                n=_bad,
                w=_plur(_bad,
                        "обязательная проблема", "обязательные проблемы",
                        "обязательных проблем",
                        "critical issue", "critical issues")))
    elif warns:
        print(t("Готово к работе. Отключено или требует внимания: {n} {w} — см. WARN выше.",
                n=warns,
                w=_plur(warns, "пункт", "пункта", "пунктов",
                        "item", "items")))
    else:
        print(t("Всё на месте."))
    return 1 if _bad else 0


if __name__ == "__main__":
    try:
        # Без консоли stdout у Python на Windows — cp1251/cp1252, и русский текст роняет
        # печать UnicodeEncodeError. Та же починка, что в webui.py.
        for _s in (sys.stdout, sys.stderr):
            try:
                cast(Any, _s).reconfigure(encoding="utf-8", errors="replace")
            except ReelsiError: raise
            except Exception:
                pass  # поток без reconfigure — печатаем как есть
        sys.exit(main())
    except ReelsiError as e:
        cli_error(e)
