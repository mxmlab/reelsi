# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""doctor.py — проверка окружения для человека на свежем клоне.

Смысл этих тестов один: doctor должен называть НАСТОЯЩУЮ причину и отличать
«сломано» от «не установлено опциональное». Отчёт, который на отсутствующий пакет
говорит «нет файла модели», отправляет человека обучать модель вместо pip install —
ровно на этом уже попадались в breath.available() (CI, 2026-08-06).

Запуск:  python -m pytest reelsi/tests -q
"""
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import doctor  # noqa: E402


@pytest.fixture(autouse=True)
def clean_rows():
    """Строки копятся в модульном списке — между тестами его надо обнулять."""
    doctor._rows = []
    doctor._bad = 0
    yield
    doctor._rows = []
    doctor._bad = 0


@pytest.fixture
def healthy(monkeypatch):
    """Заведомо здоровое окружение.

    Без этого тесты молча зависели от машины, на которой запущены: в CI нет
    ffmpeg, doctor честно ставил FAIL и возвращал 1 — а тест называл такое
    окружение здоровым и падал. Ровно та же ошибка «у меня работает», которую
    doctor и придуман ловить, только в его собственных тестах.

    Два подводных камня, на которых эта фикстура уже успела соврать:

    1. Подменять надо `doctor._which`, а НЕ `shutil.which`. Второй глобальный, и его
       зовут при импорте другие пакеты — мок ломал импорт torch, и отчёт сообщал
       «нет torch» вместо настоящей причины.
    2. Обязательные пакеты не подменяем вовсе. Заглушка вместо numpy ломала тот же
       импорт torch (он тянет numpy внутри). Эти пакеты есть и локально, и в CI.

    То же и с `subprocess.run`: не трогаем. Если ffmpeg по подделанному пути не
    запустится, doctor поставит WARN («найден, но не отвечает»), а не FAIL.

    «Здоровое окружение» включает torch (обязательная зависимость). Ослаблять
    ожидание code == 0 нельзя — иначе тест разрешает doctor молчать на битой
    машине. Поэтому тесты, которые ждут здорового исхода (code == 0 на фикстуре
    healthy), без torch пропускаются с явной причиной (pytest.importorskip), а
    тесты на отказ (code == 1) не ослабляются и проверяются всегда.
    """
    monkeypatch.setattr(doctor, "_which",
                        lambda n: "/usr/bin/" + n if n in ("ffmpeg", "node") else None)
    return True


def _run(capsys, hide=()):
    """Прогнать doctor, спрятав указанные модули. Возвращает (код, текст)."""
    saved = {}
    for name in hide:
        saved[name] = sys.modules.get(name, "__absent__")
        sys.modules[name] = None
    try:
        code = doctor.main()
    finally:
        for name, was in saved.items():
            if was == "__absent__":
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = was
    return code, capsys.readouterr().out


def test_healthy_environment_exits_zero(capsys, healthy):
    """На машине, где всё стоит, doctor не должен считать себя вправе ругаться:
    код 0, иначе им нельзя пользоваться в скриптах установки."""
    pytest.importorskip("torch", reason="здоровое окружение включает torch (обязательная зависимость)")
    code, out = _run(capsys)
    bad = "\n".join(x for x in out.splitlines() if "FAIL" in x)
    assert code == 0, f"doctor ругается на здоровом окружении:\n{bad}"
    assert "FAIL" not in out


def test_missing_core_dependency_fails(capsys, healthy):
    """Нет обязательного пакета — это FAIL и код 1. Полумеры тут вредны: человек
    иначе пойдёт запускать интерфейс, который не поднимется."""
    code, out = _run(capsys, hide=["flask"])
    assert code == 1
    assert "FAIL" in out and "flask" in out


def test_missing_optional_is_a_warning_not_a_failure(capsys, healthy):
    """Нет опционального — работать всё равно можно, код 0. И обязательно сказано,
    ЧТО именно отключится, а не просто «пакет не найден»."""
    pytest.importorskip("torch", reason="здоровое окружение включает torch (обязательная зависимость)")
    code, out = _run(capsys, hide=["silero_vad"])
    assert code == 0
    assert "silero_vad" in out
    assert doctor.t("детектор вздохов и «кхе»") in out, "не сказано, какая функция отключится"


def test_every_optional_package_names_its_feature():
    """Каждая опциональная зависимость обязана объяснять, зачем она. Список правится
    руками, и забыть описание легко — тогда отчёт превращается в перечень имён."""
    for name, feature in doctor.OPTIONAL:
        assert feature and len(feature) > 5, f"{name}: не сказано, что отключится"


def test_ffmpeg_absence_is_fatal_and_actionable(capsys, monkeypatch):
    """ffmpeg — не питоновский пакет, его отсутствие самое частое и самое непонятное.
    Причём в сообщении обязана быть команда установки: человек, у которого нет
    ffmpeg, чаще всего не знает, откуда его брать."""
    monkeypatch.setattr(doctor, "_which",
                        lambda name: None if name == "ffmpeg" else "/usr/bin/" + name)
    code, out = _run(capsys)
    assert code == 1
    line = next(x for x in out.splitlines() if "ffmpeg" in x)
    assert "FAIL" in line
    assert "install" in line.lower(), "не сказано, как поставить"


def test_plural_forms():
    """Отчёт читает человек. «1 функций» выглядит как машинная ошибка и подрывает
    доверие к остальному тексту."""
    assert doctor._plur(1, "пункт", "пункта", "пунктов") == "пункт"
    assert doctor._plur(2, "пункт", "пункта", "пунктов") == "пункта"
    assert doctor._plur(5, "пункт", "пункта", "пунктов") == "пунктов"
    assert doctor._plur(11, "пункт", "пункта", "пунктов") == "пунктов"
    assert doctor._plur(21, "пункт", "пункта", "пунктов") == "пункт"


def test_module_check_survives_broken_import(monkeypatch):
    """Сломанный пакет (не отсутствующий, а падающий на импорте) не должен ронять
    сам doctor — иначе диагностика умирает там, где нужнее всего."""
    boom = types.ModuleType("boom")

    def _raise(*a, **k):
        raise RuntimeError("сломан")

    monkeypatch.setattr(doctor.importlib, "import_module", _raise)
    ok, info = doctor._mod("что_угодно")
    assert not ok and info == "RuntimeError"
    assert boom is not None
