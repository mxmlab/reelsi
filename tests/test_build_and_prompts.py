# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракты правок 2026-08-04: два промпта генерации картинок, память формы маски
у базы вставок, «Стоп» и этапы на сборке .jsx, папки прекомпов в AE.

Запуск:  python -m pytest reelsi/tests -q
"""
import json
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import aicut  # noqa: E402
from core import xml2ae  # noqa: E402
from core.aicut import commands as aicut_commands  # noqa: E402


@pytest.fixture()
def xml_nosubs(tmp_path):
    dst = str(tmp_path / "timeline_nosubs.xml")
    shutil.copy(os.path.join(HERE, "fixtures", "timeline_nosubs.xml"), dst)
    return dst


# ---------------- два промпта генерации картинок ----------------

@pytest.fixture()
def aicut_cfg(tmp_path, monkeypatch):
    """aicut со своим ai_config — боевой (там ключи) тесты не трогают.
    Подменяем ПУТЬ, а не перезагружаем модуль: reload плодит новый объект модуля, и
    тесты, сверяющие константы по `is` (test_verify), начинают падать через раз.

Подменяем у МОДУЛЯ-ВЛАДЕЛЬЦА (aicut.config), а не у фасада `aicut`: пакет
    переэкспортирует имя, но load/save_ai_config читают свои глобалы. Патч мимо цели
    здесь — не «тест покраснел», а `save_ai_config`, пишущий в боевой конфиг с ключами.

    Конфиг создаём ПУСТЫМ заранее: иначе _seed_ai_config подхватит копию с рабочего
    ai_config.json, и результат теста начнёт зависеть от того, какие приписки к
    промптам настроены у запускающего.
    """
    cfg = tmp_path / "ai_config.json"
    cfg.write_text('{"active": "LM Studio", "profiles": {"LM Studio": {"provider": '
                   '"lmstudio", "base_url": "http://localhost:1234/v1", "api_key": "", '
                   '"model": "test"}}}', encoding="utf-8")
    monkeypatch.setattr(aicut.config, "AI_CONFIG_PATH", str(cfg))
    return aicut


def test_fixture_never_reads_the_working_config(aicut_cfg):
    """Фикстура не тянет боевой ai_config.json — иначе тесты зависят от настроек машины."""
    aicut_cfg.load_ai_config()
    cfg = aicut_cfg.config.AI_CONFIG_PATH
    assert os.path.exists(cfg) and os.path.getsize(cfg) < 200, (
        "_seed_ai_config снял копию рабочего конфига — тест читает чужие настройки")
    assert aicut_cfg.resolve_image_prompt_cfg("a")["extra"] == ""
    assert aicut_cfg.resolve_image_prompt_cfg("b")["extra"] == ""


def test_two_prompt_slots_are_independent(aicut_cfg, tmp_path, monkeypatch):
    """Слоты «a» и «b» в профиле спикера — разные приписки: ради этого вторая кнопка и заводилась
    («с текстом на фото» / «без» без похода в настройки)."""
    from core import speakers
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Тест", {
        "image_prompts": {
            "a": {"extra": "3d icon", "pos": "suffix"},
            "b": {"extra": "photo with bold caption", "pos": "prefix"},
        }
    })
    assert aicut_cfg.build_image_prompt("broken eyeglasses", slot="a", speaker="Тест") == \
        "broken eyeglasses 3d icon"
    assert aicut_cfg.build_image_prompt("broken eyeglasses", slot="b", speaker="Тест") == \
        "photo with bold caption broken eyeglasses"


def test_prompt_slot_defaults_to_first(aicut_cfg, tmp_path, monkeypatch):
    """Без слота — первый: старые вызовы и пакетная генерация ничего не заметили."""
    from core import speakers
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Тест", {
        "image_prompts": {
            "a": {"extra": "3d icon", "pos": "suffix"},
        }
    })
    assert aicut_cfg.build_image_prompt("mug", speaker="Тест") == \
        aicut_cfg.build_image_prompt("mug", slot="a", speaker="Тест")


def test_empty_second_slot_is_bare_query(aicut_cfg, tmp_path, monkeypatch):
    """Ненастроенный слот «b» генерит предмет как есть, а не падает."""
    from core import speakers
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Тест", {
        "image_prompts": {
            "a": {"extra": "3d icon", "pos": "suffix"},
        }
    })
    assert aicut_cfg.build_image_prompt("mug", slot="b", speaker="Тест") == "mug"


def test_ai_config_cleans_legacy_image_prompt_keys(aicut_cfg):
    """Задание CS: ключи image_prompt / image_prompt_b удаляются из ai_config при первом чтении."""
    cfg_file = aicut_cfg.config.AI_CONFIG_PATH
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump({"active": "LM Studio", "profiles": {
            "LM Studio": {"provider": "lmstudio", "base_url": "http://localhost:1234/v1",
                          "api_key": "", "model": "test"}},
            "image_prompt": {"extra": "legacy art", "pos": "suffix"},
            "image_prompt_b": {"extra": "legacy photo", "pos": "prefix"}
        }, f, ensure_ascii=False, indent=1)

    loaded = aicut_cfg.load_ai_config()
    assert "image_prompt" not in loaded
    assert "image_prompt_b" not in loaded

    # Проверяем, что файл на диске также перезаписан без этих ключей
    with open(cfg_file, "r", encoding="utf-8") as f:
        disk_cfg = json.load(f)
    assert "image_prompt" not in disk_cfg
    assert "image_prompt_b" not in disk_cfg


def test_speaker_image_prompts_resolution_chain(aicut_cfg, tmp_path, monkeypatch):
    """Задание CS: цепочка разрешения приписок к промпту:
    1) профиль спикера -> 2) чистый предмет (никакого дефолта в ai_config)."""
    from core import speakers
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))

    # 1. Спикеры не заданы -> чистый предмет
    assert aicut_cfg.build_image_prompt("coffee cup", slot="a") == "coffee cup"
    assert aicut_cfg.build_image_prompt("coffee cup", slot="b") == "coffee cup"

    # 2. Спикер 1 переопределяет оба слота
    speakers.save("Спикер А", {
        "image_prompts": {
            "a": {"extra": "3d icon", "pos": "suffix"},
            "b": {"extra": "photorealistic", "pos": "prefix"},
        }
    })
    # Спикер 2 задаёт свой стиль
    speakers.save("Спикер Б", {
        "image_prompts": {
            "a": {"extra": "claymation style", "pos": "prefix"},
            "b": {"extra": "neon glow", "pos": "suffix"},
        }
    })
    # Спикер 3 пустой (без image_prompts) -> чистый предмет
    speakers.save("Спикер В", {})

    # Спикер А:
    assert aicut_cfg.build_image_prompt("coffee cup", slot="a", speaker="Спикер А") == \
        "coffee cup 3d icon"
    assert aicut_cfg.build_image_prompt("coffee cup", slot="b", speaker="Спикер А") == \
        "photorealistic coffee cup"

    # Спикер Б на том же предмете даёт свои стили:
    assert aicut_cfg.build_image_prompt("coffee cup", slot="a", speaker="Спикер Б") == \
        "claymation style coffee cup"
    assert aicut_cfg.build_image_prompt("coffee cup", slot="b", speaker="Спикер Б") == \
        "coffee cup neon glow"

    # Спикер В без приписок -> чистый предмет:
    assert aicut_cfg.build_image_prompt("coffee cup", slot="a", speaker="Спикер В") == \
        "coffee cup"
    assert aicut_cfg.build_image_prompt("coffee cup", slot="b", speaker="Спикер В") == \
        "coffee cup"

    # Передача словаря напрямую (как prof)
    dict_spk = {"image_prompts": {"a": {"extra": "isometric", "pos": "suffix"}}}
    assert aicut_cfg.build_image_prompt("laptop", slot="a", speaker=dict_spk) == \
        "laptop isometric"


# ---------------- промпт вставок: русская подпись раньше английского query ----------

def test_inserts_schema_puts_prompt_before_query():
    """Поля генерируются по порядку схемы. Когда первым шёл query, слабые модели писали
    огрызок («mug»), а внятная русская подпись сочинялась уже под него."""
    props = list(aicut.INSERTS_SCHEMA["properties"]["inserts"]["items"]["properties"])
    assert props.index("prompt") < props.index("query")
    req = aicut.INSERTS_SCHEMA["properties"]["inserts"]["items"]["required"]
    assert set(req) == set(props)                 # схема strict: required == все поля


def test_markup_prompts_have_dynamic_insert_and_yellow_contracts(tmp_path, monkeypatch):
    """Квоты живут в user prompt, а жёлтые не получают искусственный бюджет."""
    words = [(i, f"word{i}", float(i), float(i + 1)) for i in range(70)]
    captured = []

    def answer(system, user, schema, **kwargs):
        captured.append((system, user))
        if schema is aicut.YELLOW_SCHEMA:
            return {"yellow": list(range(8))}
        inserts = []
        for i in range(13):
            inserts.append({"phrase": "", "type": "video" if i >= 10 else "photo",
                            "start_sec": 7.0 + i * 3, "duration_sec": 2.0,
                            "prompt": "предмет", "query": "distinct object", "mosaic": False})
        return {"analysis": "ok", "inserts": inserts}

    monkeypatch.setattr(aicut_commands, "_words_from_xml", lambda _: words)
    monkeypatch.setattr(aicut_commands, "_ask_json", answer)
    monkeypatch.setattr(xml2ae, "write_highlights",
                        lambda *a, **k: {"colored": list(range(8)), "skipped": []})
    monkeypatch.setattr(aicut_commands.time, "time", lambda: 1.0)

    yellow_path = str(tmp_path / "yellow.xml")
    yellow = aicut.cmd_yellow(yellow_path, emit=lambda *a, **k: None)
    assert len(yellow["yellow"]) == 8
    yellow_system, yellow_user = captured[0]
    assert "бюджет" not in yellow_system.lower()
    assert "квот" not in yellow_system.lower()
    assert "бюджет" not in yellow_user.lower()
    assert "квот" not in yellow_user.lower()

    inserts = aicut.cmd_inserts(str(tmp_path / "inserts.xml"), emit=lambda *a, **k: None)
    insert_system, insert_user = captured[1]
    assert "ВСЕ 10" not in insert_system and "3 видео" not in insert_system
    assert "РОВНО 13" in insert_user and "10 фото" in insert_user and "3 видео" in insert_user
    assert len(inserts["inserts"]) == 13
    assert sum(x.get("type") == "video" for x in inserts["inserts"]) == 3


def test_refill_prompt_requests_only_exact_video_deficit(monkeypatch, tmp_path):
    words = [(i, f"word{i}", float(i), float(i + 1)) for i in range(70)]
    captured = []
    avoid = [{"type": "photo", "start_sec": 7 + i * 3, "query": "old"} for i in range(10)]

    def answer(system, user, schema, **kwargs):
        captured.append(user)
        return {"analysis": "ok", "inserts": [
            {"phrase": "", "type": "video", "start_sec": 40 + i * 3,
             "duration_sec": 2, "prompt": "предмет", "query": "distinct object", "mosaic": False}
            for i in range(3)]}

    monkeypatch.setattr(aicut_commands, "_words_from_xml", lambda _: words)
    monkeypatch.setattr(aicut_commands, "_ask_json", answer)
    result = aicut.cmd_inserts(str(tmp_path / "refill.xml"), count=3, avoid=avoid,
                               emit=lambda *a, **k: None)
    assert len(result["inserts"]) == 3
    assert "0 новых фото" in captured[0] and "3 новых видео" in captured[0]
    assert "РОВНО 13" not in captured[0]


def test_cmd_inserts_refills_exact_video_deficit_end_to_end(monkeypatch, tmp_path):
    words = [(i, f"word{i}", float(i), float(i + 1)) for i in range(120)]
    calls = []

    def answer(system, user, schema, **kwargs):
        calls.append(user)
        if len(calls) == 1:
            starts = [7 + 6 * i for i in range(12)]
            inserts = [{"phrase": "", "type": "photo", "start_sec": s,
                        "duration_sec": 2, "prompt": "предмет", "query": "distinct object",
                        "mosaic": False} for s in starts]
            inserts.append({"phrase": "", "type": "video", "start_sec": 82,
                            "duration_sec": 2, "prompt": "сцена", "query": "distinct scene",
                            "mosaic": False})
        else:
            inserts = [{"phrase": "", "type": "video", "start_sec": s,
                        "duration_sec": 2, "prompt": "сцена", "query": "distinct scene",
                        "mosaic": False} for s in (104, 110)]
        return {"analysis": "ok", "inserts": inserts}

    monkeypatch.setattr(aicut_commands, "_words_from_xml", lambda _: words)
    monkeypatch.setattr(aicut_commands, "_ask_json", answer)
    result = aicut.cmd_inserts(str(tmp_path / "e2e.xml"), emit=lambda *a, **k: None)
    out = result["inserts"]
    assert len(calls) == 2
    assert "0 новых фото" in calls[1] and "2 новых видео" in calls[1]
    assert "РОВНО 13" not in calls[1] and "count" not in calls[1].lower()
    assert len(out) == 13
    assert sum(x.get("type") == "video" for x in out) == 3
    assert sum(x.get("type") != "video" for x in out) == 10


# ---------------- база вставок: память формы маски (mw/mh) ----------------

@pytest.fixture()
def insertlib(tmp_path, monkeypatch):
    """Свой индекс базы вставок. Подменяем путь и чистим кэш (см. aicut_cfg о reload)."""
    from core import insertlib as _il
    monkeypatch.setattr(_il, "INDEX_PATH", str(tmp_path / "insertlib.json"))
    monkeypatch.setattr(_il, "_CACHE", {"mtime": 0, "data": None})
    return _il


def test_crop_of_ignores_default_and_junk(insertlib):
    """100/100 — «как считает JSX сам», запоминать нечего. Мусор и выход за пределы
    скраббера (20..300) в индекс попасть не должны."""
    assert insertlib._crop_of({"mw": 100, "mh": 100}) is None
    assert insertlib._crop_of({"mw": None, "mh": 80}) is None
    assert insertlib._crop_of({"mw": 4000, "mh": 80}) is None
    assert insertlib._crop_of({"mw": 72, "mh": 118}) == (72, 118)


def test_adopt_remembers_crop_and_match_returns_it(insertlib, tmp_path):
    """Ради чего всё: картинка из базы приезжает уже с той формой маски, с которой
    ушла в проект, а не подгоняется скрабберами в каждом ролике заново."""
    base = tmp_path / "lib"
    (base / "photos").mkdir(parents=True)
    img = base / "photos" / "mug.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    insertlib._save({"dirs": [str(base)], "emb_model": "", "emb_tag": insertlib.EMB_TAG,
                     "items": [{"path": str(img), "name": "mug.png", "type": "photo",
                                "used": 0, "desc": "coffee mug", "emb": None}]})
    insertlib.adopt([{"path": str(img), "desc": "coffee mug", "mw": 72, "mh": 118}],
                    str(base))
    res = insertlib.match_many(["coffee mug"], k=3)[0]
    assert res and res[0]["mw"] == 72 and res[0]["mh"] == 118


def test_rescan_keeps_remembered_crop(insertlib, tmp_path):
    """Пересканировать базу — обычное дело; форма маски это переживает (как rej/added)."""
    base = tmp_path / "lib"
    (base / "photos").mkdir(parents=True)
    img = base / "photos" / "mug.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    insertlib._save({"dirs": [str(base)], "emb_model": "", "emb_tag": insertlib.EMB_TAG,
                     "items": [{"path": str(img), "name": "mug.png", "type": "photo",
                                "used": 0, "desc": "mug", "emb": None, "mw": 72, "mh": 118}]})
    insertlib.build_index([str(base)], use_emb=False)
    it = next(x for x in insertlib._load()["items"] if x["name"] == "mug.png")
    assert (it.get("mw"), it.get("mh")) == (72, 118)


# ---------------- сборка .jsx: «Стоп» и этапы ----------------

def test_build_is_cancellable(xml_nosubs, tmp_path):
    """«Стоп» на сборке обязан прерывать её ВНУТРИ файла: раньше флаг смотрели только
    между файлами, и весь долгий кусок (рото) досчитывался до конца."""
    out = str(tmp_path / "stop.jsx")
    with pytest.raises(xml2ae.Cancelled):
        xml2ae.to_ae_full(xml_nosubs, out, emit=lambda *a: None, cancel=lambda: True)
    assert not os.path.exists(out)          # оборванная сборка файл не пишет


def test_build_reports_stages(xml_nosubs, tmp_path):
    """Сборка должна говорить, на каком она этапе, — иначе непонятно, идёт ли она вообще."""
    log = []
    xml2ae.to_ae_full(xml_nosubs, str(tmp_path / "s.jsx"), emit=log.append)
    assert any("разбор XML" in s for s in log)
    assert any("сборка скрипта" in s for s in log)


def test_combined_reports_progress_and_stops(xml_nosubs, tmp_path):
    """«Один .jsx на всё» раньше молчал весь прогон и «Стоп» не проверял вовсе."""
    log, seen = [], []
    jobs = [{"xml_path": xml_nosubs}, {"xml_path": xml_nosubs}]
    path, n = xml2ae.build_combined(jobs, str(tmp_path / "all.jsx"), emit=log.append,
                                    progress=lambda i, t: seen.append((i, t)))
    assert n == 2 and seen == [(1, 2), (2, 2)]
    assert any("[1/2]" in s for s in log) and any("[2/2]" in s for s in log)
    with pytest.raises(xml2ae.Cancelled):
        xml2ae.build_combined(jobs, str(tmp_path / "all2.jsx"), emit=log.append,
                              cancel=lambda: True)


# ---------------- AE: прекомпы по папкам ----------------

def test_precomps_go_to_bins(xml_nosubs, tmp_path):
    """В корне панели проекта — только основные композиции. Интро и субтитры уезжают
    в свои бины, как камеры/вставки/рото."""
    path, _, _ = xml2ae.to_ae_full(xml_nosubs, str(tmp_path / "bins.jsx"),
                                   emit=lambda *a: None)
    src = open(path, encoding="utf-8-sig").read()
    assert 'toBin(ic,"Интро")' in src
    assert 'toBin(subc,"Субтитры")' in src


def _fresh_job():
    """Пустой JOB для одного прогона сборки: лог и списки копятся между тестами."""
    from api import build as apibuild
    from api._core import JOB
    JOB.update(log=[], results=[], failed=[], cancel=False, log_base=0)
    return apibuild, JOB


def _log_text(log_list):
    """Сборка текстового лога из списка строк или структурных записей (по-русски)."""
    return "\n".join(e["t"].format(**e.get("v", {})) if isinstance(e, dict) and "t" in e else str(e) for e in log_list)


def test_несуществующая_папка_jsx_создаётся_а_не_подменяется(tmp_path):
    """Непустая несуществующая папка для .jsx СОЗДАЁТСЯ, а не роняет сборку.

    Раньше здесь был молчаливый фолбэк «нет папки -> кладём рядом с XML»: человек
    правил поле, жал сборку и находил файл где угодно, только не там, куда просил
    (жалоба 2026-08-11). Сначала сделали честный отказ, теперь (2026-08-12) папка
    создаётся сама (os.makedirs exist_ok) и это видно в логе. Молчаливой подмены
    пути по-прежнему быть не должно: сборка обязана идти ИМЕННО в указанную папку,
    а не в соседнюю — об этом следят две проверки ниже.
    """
    apibuild, JOB = _fresh_job()
    dst = tmp_path / "нет-такой-папки"
    apibuild._run_build_job([{"xml_path": str(tmp_path / "01_clip.xml")}],
                            "separate", str(dst))
    assert dst.is_dir(), "несуществующая папка для .jsx обязана была создаться"
    log = _log_text(JOB["log"])
    assert "Папка создана" in log and str(dst) in log, (
        "в логе не сказано, что папка создана, и какая именно")


def test_папка_jsx_не_создалась_ошибка_с_причиной_а_не_тихая_подмена(tmp_path):
    """Создать папку не вышло (нет прав/кривой путь) — честная ошибка с причиной.

    Ошибка остаётся ТОЛЬКО когда os.makedirs не смог (нет прав, кривой путь): текст
    причины печатается в лог. Молчаливый фолбэк на папку XML — подмена пути, её
    чинили в 2026-08-11 и она не должна вернуться даже под видом «не смог создать».
    """
    apibuild, JOB = _fresh_job()
    blocker = tmp_path / "файл.txt"
    blocker.write_text("это файл, а не папка", encoding="utf-8")
    apibuild._run_build_job([{"xml_path": str(tmp_path / "01_clip.xml")}],
                            "separate", str(blocker / "внутри"))
    assert JOB["failed"], "сборка с несоздаваемой папкой обязана падать"
    assert not JOB["results"], "ничего не должно быть собрано"
    log = _log_text(JOB["log"])
    assert "не удалось создать папку" in log and str(blocker) in log, (
        "в логе нет причины, почему папка не создалась")


def test_папка_jsx_печатается_в_лог(tmp_path):
    """Куда уедет .jsx — видно в логе ДО начала работы, полным путём.

    «Куда положил» — первый вопрос к логу сборки, и раньше ответа в нём не было:
    печаталось только имя файла, а папка не печаталась нигде.
    """
    apibuild, JOB = _fresh_job()
    dst = tmp_path / "Reelsi_out"
    dst.mkdir()
    apibuild._run_build_job([{"xml_path": str(tmp_path / "нет.xml")}], "separate", str(dst))
    assert str(dst) in _log_text(JOB["log"]), "папка для .jsx не названа в логе"


def test_каждый_клип_собирается_в_свою_папку_по_тегу(xml_nosubs, tmp_path):
    """Папка .jsx у клипа определяется ЕГО тегом спикера: клипы двух
    спикеров в одном наборе собираются каждый в свою папку, а не в общую.
    per-job outdir перекрывает глобальное поле только для этого клипа."""
    apibuild, JOB = _fresh_job()
    a = str(tmp_path / "спикер_A")
    b = str(tmp_path / "спикер_B")
    norm = [
        {"xml_path": xml_nosubs, "outdir": a},
        {"xml_path": xml_nosubs, "outdir": b},
    ]
    apibuild._run_build_job(norm, "separate", str(tmp_path / "глобальная"))
    log = _log_text(JOB["log"])
    assert JOB["results"], "ничего не собралось"
    # каждый .jsx лёг в папку СВОЕГО спикера, а не в глобальную
    assert os.path.isfile(os.path.join(a, "timeline_nosubs.jsx")), "первый клип не в своей папке"
    assert os.path.isfile(os.path.join(b, "timeline_nosubs.jsx")), "второй клип не в своей папке"
    assert not os.path.isdir(str(tmp_path / "глобальная")), (
        "глобальная папка создана, хотя у всех клипов есть своя — подмена пути вернулась")
    assert "Папка для .jsx: " + a in log and "Папка для .jsx: " + b in log, (
        "обе папки не названы в логе")


def test_глобальная_папка_остаётся_запасной_для_клипа_без_тега(xml_nosubs, tmp_path):
    """Клип без тега спикера собирается в глобальное поле, как раньше (
    «клип без спикера — как сегодня»): per-job outdir пуст — берётся общая папка."""
    apibuild, JOB = _fresh_job()
    dst = str(tmp_path / "общая")
    apibuild._run_build_job([{"xml_path": xml_nosubs, "outdir": None}], "separate", dst)
    assert JOB["results"], "ничего не собралось"
    assert os.path.isfile(os.path.join(dst, "timeline_nosubs.jsx")), (
        "клип без тега не лёг в глобальную папку")


def test_общий_jsx_собирается_когда_у_клипа_своя_папка(xml_nosubs, tmp_path):
    """«Один .jsx на всё» падал с TypeError: outdir (папка клипа по тегу спикера
 ) уезжал в build_combined -> to_ae_full(**kw) -> scene_plan, который
    такого параметра не знает. Папка сборки — не параметр плана сцены."""
    apibuild, JOB = _fresh_job()
    dst = str(tmp_path / "общая")
    norm = [{"xml_path": xml_nosubs, "outdir": str(tmp_path / "спикер_A")},
            {"xml_path": xml_nosubs, "outdir": None}]
    apibuild._run_build_job(norm, "combined", dst)
    log = _log_text(JOB["log"])
    assert "ОШИБКА" not in log, log
    assert JOB["results"], "общий .jsx не собрался"
    assert os.path.isfile(os.path.join(dst, "Reelsi_all.jsx"))


def test_clip_build_failure_recorded_in_failed_and_queue(xml_nosubs, tmp_path, monkeypatch):
    """Сборка набора из двух клипов, xml2ae.to_ae_full для одного подменён на исключение:
    в JOB['failed'] ровно один элемент с именем этого клипа, второй клип в results,
    элемент очереди упавшего не на stage='jsx'."""
    apibuild, JOB = _fresh_job()
    dst = str(tmp_path / "out")
    failing_xml = str(tmp_path / "01_fail.xml")
    ok_xml = str(tmp_path / "02_ok.xml")
    shutil.copy(xml_nosubs, failing_xml)
    shutil.copy(xml_nosubs, ok_xml)

    orig_to_ae_full = xml2ae.to_ae_full

    def mock_to_ae_full(xml_path, *args, **kwargs):
        if "01_fail" in xml_path:
            raise RuntimeError("тестовая ошибка сборки клипа")
        return orig_to_ae_full(xml_path, *args, **kwargs)

    monkeypatch.setattr(xml2ae, "to_ae_full", mock_to_ae_full)
    norm = [
        {"xml_path": failing_xml},
        {"xml_path": ok_xml},
    ]
    apibuild._run_build_job(norm, "separate", dst)

    # в JOB["failed"] ровно один элемент с именем этого клипа
    assert len(JOB["failed"]) == 1
    assert JOB["failed"][0]["name"] == "01_fail"
    assert "тестовая ошибка сборки клипа" in JOB["failed"][0]["reason"]

    # второй клип в results
    assert len(JOB["results"]) == 1
    assert "02_ok.jsx" in JOB["results"][0]

    # элемент очереди упавшего не на stage="jsx"
    items_by_name = {it["name"]: it for it in JOB.get("items", [])}
    assert "01_fail" in items_by_name
    assert items_by_name["01_fail"]["stage"] != "jsx"
    assert items_by_name["01_fail"]["stage"] == "error"

