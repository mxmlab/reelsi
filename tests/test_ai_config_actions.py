# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание NZ: `/api/ai_config` — эталон ответов всех действий (снят ДО переноса).

Действия роута переехали в `core/aicut/config_actions.py`, ответ собирает одна функция.
Контракт при этом не меняется, и проверяется он не «на глаз», а сверкой с эталоном:

`tests/fixtures/ai_config_actions.json` — ответы Flask test client на ВСЕ действия
(успех и типовой отказ на каждое) плюс состояние `ai_config.json` после каждого шага,
снятые на СТАРОМ коде. Здесь тот же стенд прогоняется ещё раз и сверяется с эталоном
дословно; отдельно проверяется, что каждый сценарий попал в СВОЮ ветку (код ошибки).

Ключи в стенде заведомо ненастоящие, и в эталон они не попадают даже сырьём: состояние
конфига пишется с ключами, закрытыми маской (`_mask_key`).

Пересобрать эталон (только осознанно, вместе с правкой контракта):

    python tests/test_ai_config_actions.py tests/fixtures/ai_config_actions.json

Прогон вне pytest — нарочно: у pytest свой сторож изоляции, который валит сессию за
новый файл в репозитории.

Запуск теста: python -m pytest tests/test_ai_config_actions.py -q
"""
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from unittest import mock

from flask import Flask

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FIXTURE = os.path.join(HERE, "fixtures", "ai_config_actions.json")
SETTINGS_JS = os.path.join(ROOT, "static", "app", "10-settings.js")

# Временный каталог стенда — только для прогона `__main__` (пересборка эталона).
_TMP = None

if __name__ == "__main__":
    # Пересборка эталона: свои REELSI_* ДО импорта api — иначе стенд связал бы пути
    # с боевыми файлами рабочей копии (ai_config.json с ключами в том числе).
    _TMP = tempfile.mkdtemp(prefix="reelsi-nz-capture-")
    for _var, _name in (("REELSI_AI_CONFIG", "ai_config.json"),
                        ("REELSI_MODELS_DEV", "models_dev.json"),
                        ("REELSI_AI_LOG", "ai_calls.jsonl"),
                        ("REELSI_UI_STATE", "ui_state.json"),
                        ("REELSI_JOB_LOCK", "job.lock"),
                        ("REELSI_JOB_STATE", "job_state.json"),
                        ("REELSI_TERMS", "terms.json"),
                        ("REELSI_BADWORDS", "badwords.user.txt"),
                        ("REELSI_OKWORDS", "okwords.user.txt"),
                        ("REELSI_INSERTLIB", "insertlib.json"),
                        ("REELSI_RENDER_STATS", "render_stats.json"),
                        ("REELSI_LOG", "reelsi.log")):
        os.environ[_var] = os.path.join(_TMP, _name)
    os.environ["REELSI_VIDEO_DIR"] = os.path.join(_TMP, "_videogen")
    os.environ["REELSI_VIDEO_HISTORY"] = os.path.join(_TMP, "_videogen", "history.json")

import api  # noqa: E402
from core.aicut import config as aicut_config  # noqa: E402


# Ключи — подставные: ровный ряд одного символа, хвост — тот, что показывает маска.
# Энтропии такого значения не хватает порогу generic-api-key в gitleaks (3.5),
# поэтому сканер в CI молчит на этом файле без исключений по пути.
START_CFG = {
    "active": "Основной",
    "profiles": {
        "Основной": {"provider": "lmstudio", "base_url": "http://localhost:1234/v1",
                     "api_key": "xxxxxxxxxxxxxxxx1111",
                     "model": "qwen3.6-27b-4bpw-16gb-vram"},
        "Клод": {"provider": "anthropic", "base_url": "https://api.anthropic.com",
                 "api_key": "xxxxxxxxxxxxxxxx2222", "model": "claude-sonnet-5"},
        "Аудио": {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1",
                  "api_key": "xxxxxxxxxxxxxxxx3333", "model": "google/gemini-2.5-flash"},
        # env-ключ: наружу уходит имя переменной, а не значение (маска тут не нужна)
        "Текстовая": {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1",
                      "api_key": "env:NZ_TEST_KEY_MISSING",
                      "model": "deepseek/deepseek-v4-flash"},
    },
}

# Все действия роута: успех и типовой отказ на каждое. Порядок важен — состояние
# конфига накапливается так же, как в живом интерфейсе (переименование переносит
# привязки, удаление профиля их снимает и т. п.).
SCENARIOS = [
    {"name": "GET: начальное состояние", "method": "GET"},

    # ---- set_reasoning_step ----
    {"name": "set_reasoning_step: успех (cut=high)", "method": "POST",
     "body": {"action": "set_reasoning_step", "step": "cut", "level": "high"}},
    {"name": "set_reasoning_step: успех (cut=max, память на модель)", "method": "POST",
     "body": {"action": "set_reasoning_step", "step": "cut", "level": "max"}},
    {"name": "set_reasoning_step: неизвестный шаг", "method": "POST",
     "body": {"action": "set_reasoning_step", "step": "нетакого", "level": "high"},
     "expect": "unknown_step"},
    {"name": "set_reasoning_step: неизвестный уровень", "method": "POST",
     "body": {"action": "set_reasoning_step", "step": "cut", "level": "мусор"},
     "expect": "unknown_reasoning_level"},

    # ---- set_step_profile ----
    {"name": "set_step_profile: успех (yellow=Клод)", "method": "POST",
     "body": {"action": "set_step_profile", "step": "yellow", "name": "Клод"}},
    {"name": "set_step_profile: сброс шага на общий active", "method": "POST",
     "body": {"action": "set_step_profile", "step": "yellow", "name": ""}},
    {"name": "set_step_profile: успех (intro=Аудио)", "method": "POST",
     "body": {"action": "set_step_profile", "step": "intro", "name": "Аудио"}},
    {"name": "set_step_profile: неизвестный шаг", "method": "POST",
     "body": {"action": "set_step_profile", "step": "нетакого", "name": "Клод"},
     "expect": "unknown_step"},
    {"name": "set_step_profile: нет профиля", "method": "POST",
     "body": {"action": "set_step_profile", "step": "yellow", "name": "НетТакого"},
     "expect": "no_profile"},

    # ---- set_active ----
    {"name": "set_active: успех (Клод)", "method": "POST",
     "body": {"action": "set_active", "name": "Клод"}},
    {"name": "set_active: нет профиля", "method": "POST",
     "body": {"action": "set_active", "name": "НетТакого"}, "expect": "no_profile"},

    # ---- set_active_omni ----
    {"name": "set_active_omni: локальный движок", "method": "POST",
     "body": {"action": "set_active_omni", "name": "__local__"}},
    {"name": "set_active_omni: успех (аудио-модель)", "method": "POST",
     "body": {"action": "set_active_omni", "name": "Аудио"}},
    {"name": "set_active_omni: Claude не принимает аудио", "method": "POST",
     "body": {"action": "set_active_omni", "name": "Клод"}, "expect": "claude_no_audio"},
    {"name": "set_active_omni: модель не принимает аудио", "method": "POST",
     "body": {"action": "set_active_omni", "name": "Текстовая"},
     "expect": "omni_audio_unsupported"},
    {"name": "set_active_omni: нет профиля", "method": "POST",
     "body": {"action": "set_active_omni", "name": "НетТакого"}, "expect": "no_profile"},

    # ---- set_active_cut_asr ----
    {"name": "set_active_cut_asr: успех (gigaam)", "method": "POST",
     "body": {"action": "set_active_cut_asr", "name": "gigaam"}},
    {"name": "set_active_cut_asr: движок не годен для нарезки", "method": "POST",
     "body": {"action": "set_active_cut_asr", "name": "whisper"},
     "expect": "invalid_cut_asr"},

    # ---- set_active_image ----
    {"name": "set_active_image: успех (Аудио)", "method": "POST",
     "body": {"action": "set_active_image", "name": "Аудио"}},
    {"name": "set_active_image: выключено", "method": "POST",
     "body": {"action": "set_active_image", "name": "__off__"}},
    {"name": "set_active_image: провайдер без картинок", "method": "POST",
     "body": {"action": "set_active_image", "name": "Клод"}, "expect": "provider_no_images"},
    {"name": "set_active_image: нет профиля", "method": "POST",
     "body": {"action": "set_active_image", "name": "НетТакого"}, "expect": "no_profile"},

    # ---- set_active_video ----
    {"name": "set_active_video: успех (Аудио)", "method": "POST",
     "body": {"action": "set_active_video", "name": "Аудио"}},
    {"name": "set_active_video: выключено", "method": "POST",
     "body": {"action": "set_active_video", "name": "__off__"}},
    {"name": "set_active_video: провайдер без видео", "method": "POST",
     "body": {"action": "set_active_video", "name": "Клод"}, "expect": "provider_no_video"},
    {"name": "set_active_video: нет профиля", "method": "POST",
     "body": {"action": "set_active_video", "name": "НетТакого"}, "expect": "no_profile"},

    # ---- set_video_model / set_video_resolution ----
    {"name": "set_video_resolution: успех (1080p)", "method": "POST",
     "body": {"action": "set_video_resolution", "resolution": "1080p"}},
    {"name": "set_video_resolution: модель не поддерживает (2K)", "method": "POST",
     "body": {"action": "set_video_resolution", "resolution": "2K"},
     "expect": "video_resolution_unsupported"},
    {"name": "set_video_model: успех + сброс устаревшего разрешения", "method": "POST",
     "body": {"action": "set_video_model", "model": "bytedance/seedance-2.0-fast"}},
    {"name": "set_video_model: неизвестная модель (разрешение не трогаем)", "method": "POST",
     "body": {"action": "set_video_model", "model": "некая/неизвестная-модель"}},
    {"name": "set_video_resolution: успех при неизвестной модели (4K)", "method": "POST",
     "body": {"action": "set_video_resolution", "resolution": "4K"}},
    {"name": "set_video_resolution: пусто — решает провайдер", "method": "POST",
     "body": {"action": "set_video_resolution", "resolution": ""}},

    # ---- set_image_rembg / set_glitch_glow ----
    {"name": "set_image_rembg: выключить", "method": "POST",
     "body": {"action": "set_image_rembg", "value": False}},
    {"name": "set_image_rembg: включить", "method": "POST",
     "body": {"action": "set_image_rembg", "value": True}},
    {"name": "set_glitch_glow: успех (deepglow2)", "method": "POST",
     "body": {"action": "set_glitch_glow", "value": "deepglow2"}},
    {"name": "set_glitch_glow: недопустимый режим", "method": "POST",
     "body": {"action": "set_glitch_glow", "value": "мусор"},
     "expect": "glitch_glow_mode_invalid"},

    # ---- save_profile ----
    {"name": "save_profile: новый профиль + set_active", "method": "POST",
     "body": {"action": "save_profile", "name": "Новый", "set_active": True,
              "profile": {"provider": "openai", "base_url": "http://localhost:9999/v1",
                          "api_key": "xxxxxxxxxxxxxxxx4444", "model": "gpt-4o",
                          "headers_text": "X-Foo: bar\nX-Baz: qux"}}},
    {"name": "save_profile: ключ-маска при том же адресе — ключ сохранён", "method": "POST",
     "body": {"action": "save_profile", "name": "Новый",
              "profile": {"provider": "openai", "base_url": "http://localhost:9999/v1",
                          "api_key": "•••4444", "model": "gpt-4o"}}},
    {"name": "save_profile: ключ-маска + другой адрес — отказ", "method": "POST",
     "body": {"action": "save_profile", "name": "Новый",
              "profile": {"provider": "openai", "base_url": "http://чужой.example/v1",
                          "api_key": "•••4444", "model": "gpt-4o"}},
     "expect": "key_mask_address_changed"},
    {"name": "save_profile: ключ-маска + другой провайдер — отказ", "method": "POST",
     "body": {"action": "save_profile", "name": "Новый",
              "profile": {"provider": "openrouter",
                          "base_url": "https://openrouter.ai/api/v1",
                          "api_key": "•••4444", "model": "gpt-4o"}},
     "expect": "key_mask_address_changed"},
    {"name": "save_profile: пустое имя", "method": "POST",
     "body": {"action": "save_profile", "name": "   ",
              "profile": {"provider": "openai", "api_key": "xxxxxxxxxxxxxxxx4444"}},
     "expect": "empty_profile_name"},
    {"name": "save_profile: profile не объект", "method": "POST",
     "body": {"action": "save_profile", "name": "Новый", "profile": "строка"},
     "expect": "bad_profile"},
    {"name": "save_profile: переименование с ключом-маской (Клод → Клод Новый)",
     "method": "POST",
     "body": {"action": "save_profile", "name": "Клод Новый", "old_name": "Клод",
              "profile": {"provider": "anthropic", "base_url": "https://api.anthropic.com",
                          "api_key": "•••2222", "model": "claude-sonnet-5"}}},
    {"name": "save_profile: переименование активного (Новый → Новый 2)", "method": "POST",
     "body": {"action": "save_profile", "name": "Новый 2", "old_name": "Новый",
              "profile": {"provider": "openai", "base_url": "http://localhost:9999/v1",
                          "api_key": "•••4444", "model": "gpt-4o"}}},

    # Перед переименованием «Аудио» возвращаем генерацию картинок/видео на него:
    # так один сценарий проверяет перенос ВСЕХ привязок сразу.
    {"name": "set_active_image: снова Аудио (перед переименованием)", "method": "POST",
     "body": {"action": "set_active_image", "name": "Аудио"}},
    {"name": "set_active_video: снова Аудио (перед переименованием)", "method": "POST",
     "body": {"action": "set_active_video", "name": "Аудио"}},
    {"name": "save_profile: переименование Аудио → Аудио 2 (переносит привязки)",
     "method": "POST",
     "body": {"action": "save_profile", "name": "Аудио 2", "old_name": "Аудио",
              "profile": {"provider": "openrouter",
                          "base_url": "https://openrouter.ai/api/v1",
                          "api_key": "•••3333", "model": "google/gemini-2.5-flash"}}},

    # ---- clone_profile ----
    {"name": "clone_profile: успех", "method": "POST",
     "body": {"action": "clone_profile", "name": "Аудио 2", "new_name": "Копия"}},
    {"name": "clone_profile: пустое имя", "method": "POST",
     "body": {"action": "clone_profile", "name": "  ", "new_name": "Копия 2"},
     "expect": "empty_profile_name"},
    {"name": "clone_profile: пустое имя копии", "method": "POST",
     "body": {"action": "clone_profile", "name": "Аудио 2", "new_name": "  "},
     "expect": "empty_clone_name"},
    {"name": "clone_profile: нет профиля", "method": "POST",
     "body": {"action": "clone_profile", "name": "НетТакого", "new_name": "Копия 3"},
     "expect": "no_profile"},
    {"name": "clone_profile: имя занято", "method": "POST",
     "body": {"action": "clone_profile", "name": "Аудио 2", "new_name": "Копия"},
     "expect": "profile_exists"},

    # ---- delete_profile ----
    {"name": "delete_profile: успех (неактивный)", "method": "POST",
     "body": {"action": "delete_profile", "name": "Копия"}},
    {"name": "delete_profile: нет профиля", "method": "POST",
     "body": {"action": "delete_profile", "name": "НетТакого"}, "expect": "no_profile"},
    {"name": "delete_profile: удаление активного — active переезжает", "method": "POST",
     "body": {"action": "delete_profile", "name": "Новый 2"}},
    {"name": "delete_profile: удаление шаговой привязки (Клод Новый)", "method": "POST",
     "body": {"action": "delete_profile", "name": "Клод Новый"}},
    {"name": "delete_profile: удаление привязанного к картинкам/видео (Аудио 2)",
     "method": "POST",
     "body": {"action": "delete_profile", "name": "Аудио 2"}},
    {"name": "delete_profile: успех (Текстовая)", "method": "POST",
     "body": {"action": "delete_profile", "name": "Текстовая"}},
    {"name": "delete_profile: последний профиль", "method": "POST",
     "body": {"action": "delete_profile", "name": "Основной"}, "expect": "last_profile"},

    # ---- диспетчер ----
    {"name": "неизвестное действие", "method": "POST",
     "body": {"action": "нетакого"}, "expect": "unknown_action"},
    {"name": "тело без action", "method": "POST", "body": {}, "expect": "unknown_action"},

    {"name": "GET: финальное состояние", "method": "GET"},
]


class _FakeResp:
    """Ответ заглушки сети: вызывающие читают его только через json.load -> read()."""

    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(req, timeout=None):
    """Сеть в стенде запрещена: отвечает только каталог моделей OpenRouter.

    Он нужен проверке «умеет ли модель профиля слушать звук» (`_omni_audio_check`) —
    без него ветка `omni_audio_unsupported` не проверяется вовсе. Всё остальное, в том
    числе каталог models.dev, — отказ: тогда возможности моделей «неизвестны», и ответ
    не зависит от того, есть ли у машины интернет.
    """
    url = getattr(req, "full_url", str(req))
    if url.startswith("https://openrouter.ai/api/v1/models"):
        return _FakeResp({"data": [
            {"id": "google/gemini-2.5-flash",
             "architecture": {"input_modalities": ["text", "image", "audio"]}},
            {"id": "deepseek/deepseek-v4-flash",
             "architecture": {"input_modalities": ["text"]}},
        ]})
    raise urllib.error.URLError("сеть в стенде запрещена")


@contextlib.contextmanager
def _video_catalog_cleared():
    """Пустой каталог видео-моделей на время прогона.

    VIDEO_MODEL_CAPS — глобал процесса: соседний тест мог его наполнить, и тогда
    video_caps вернул бы чужие возможности. Словарь чистим на месте (его идентичность
    держат импортёры aicut.VIDEO_MODEL_CAPS) и возвращаем как было.
    """
    from core.aicut import video
    saved = dict(video.VIDEO_MODEL_CAPS)
    video.VIDEO_MODEL_CAPS.clear()
    try:
        yield
    finally:
        video.VIDEO_MODEL_CAPS.clear()
        video.VIDEO_MODEL_CAPS.update(saved)


def _mask_key(k):
    """Как ключ видит интерфейс: env:VAR — имя переменной, иначе «•••xxxx».

    Своя копия, а не вызов боевой: эталон снимался ДО переноса масок в core, и стенд
    снятия не должен зависеть от кода, который он же и проверяет.
    """
    if isinstance(k, str) and k.strip().startswith("env:"):
        return k
    return ("•••" + k[-4:]) if k else ""


def _config_state(cfg_path):
    """Состояние ai_config.json после шага: как есть, но ключи — маской.

    Сырые ключи в эталон не попадают вовсе: он лежит в репозитории.
    """
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    for p in (cfg.get("profiles") or {}).values():
        if isinstance(p, dict) and "api_key" in p:
            p["api_key"] = _mask_key(p["api_key"])
    return cfg


def _branch(rec):
    """В какую ветку попал сценарий: код ошибки, None — успех, http_N — не 200."""
    if rec["status"] != 200:
        return "http_%d" % rec["status"]
    js = rec.get("json") or {}
    if js.get("ok") is True:
        return None
    return js.get("err")


def run_scenarios(cfg_path):
    """Прогнать все сценарии подряд на конфиге cfg_path и вернуть записи ответов.

    Конфиг начинается с START_CFG и дальше меняется действиями — как в живом UI.
    Каждая запись: имя сценария, тело запроса, статус, JSON ответа и состояние
    конфига на диске после шага.
    """
    from core.aicut import catalog

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(START_CFG, f, ensure_ascii=False, indent=1)

    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True

    out = []
    prev_cfg = None
    client = app.test_client()
    with mock.patch.object(urllib.request, "urlopen", _fake_urlopen), \
            mock.patch.object(catalog, "_CATALOG", None), \
            mock.patch.object(catalog, "_CATALOG_TS", 0.0), \
            _video_catalog_cleared():
        for sc in SCENARIOS:
            if sc["method"] == "GET":
                r = client.get("/api/ai_config")
            else:
                r = client.post("/api/ai_config", json=sc["body"])
            js = r.get_json(silent=True)
            rec = {"name": sc["name"], "method": sc["method"],
                   "request": sc.get("body"), "status": r.status_code, "json": js}
            if js is None:                     # не-JSON: 500-страница и т. п.
                rec["text"] = r.get_data(as_text=True)[:200]
            # Состояние конфига на диске — только когда оно изменилось: в эталоне это
            # журнал изменений (null = «как на прошлом шаге»), а не 63 копии одного и
            # того же файла. Ловит то, чего не видно в ответе: reasoning_by_model,
            # сырые ключи профилей, порядок профилей.
            state = _config_state(cfg_path)
            rec["config"] = None if state == prev_cfg else state
            prev_cfg = state
            out.append(rec)
    return out


def _load_reference():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _diff_rec(got, want):
    """Человекочитаемая разница двух записей — по полям верхнего уровня."""
    bad = [k for k in sorted(set(got) | set(want)) if got.get(k) != want.get(k)]
    lines = []
    for k in bad:
        lines.append("%s:\n  эталон:  %s\n  сейчас:  %s" % (
            k,
            json.dumps(want.get(k), ensure_ascii=False, sort_keys=True)[:600],
            json.dumps(got.get(k), ensure_ascii=False, sort_keys=True)[:600]))
    return "\n".join(lines)


def test_ai_config_actions_match_reference():
    """Все действия отвечают ровно как до переноса — сверка с эталоном по шагам."""
    records = run_scenarios(aicut_config.AI_CONFIG_PATH)
    reference = _load_reference()
    assert len(records) == len(reference), (
        "сценариев %d, в эталоне %d — список SCENARIOS менялся без пересборки эталона"
        % (len(records), len(reference)))

    # 1. Каждый сценарий попал в СВОЮ ветку: иначе сверка ниже сравнивала бы два
    #    одинаково неверных ответа (например, «неизвестное действие» вместо отказа).
    for rec, sc in zip(records, SCENARIOS):
        assert rec["name"] == sc["name"]
        assert _branch(rec) == sc.get("expect"), (
            "сценарий «%s»: ветка %r вместо %r (ответ: %s)"
            % (sc["name"], _branch(rec), sc.get("expect"),
               json.dumps(rec.get("json"), ensure_ascii=False)[:400]))

    # 2. Дословная сверка ответов и состояния конфига с эталоном.
    for got, want in zip(records, reference):
        assert got == want, "сценарий «%s» разошёлся с эталоном:\n%s" % (
            got["name"], _diff_rec(got, want))

    # 3. Ни один сырой ключ из стенда не ушёл в ответы и в эталон.
    blob = json.dumps(records, ensure_ascii=False)
    for prof in START_CFG["profiles"].values():
        key = prof["api_key"]
        if not key.startswith("env:"):
            assert key not in blob, "сырой ключ профиля попал в ответ"


def test_actions_table_covers_every_ui_action():
    """Таблица ACTIONS — ровно те действия, которые шлёт интерфейс.

    Диспетчер роута и фронт — две стороны одного контракта: забытое действие падало бы
    в «неизвестное действие» уже у пользователя, лишнее — мёртвый код.
    """
    from core.aicut.config_actions import ACTIONS
    with open(SETTINGS_JS, encoding="utf-8") as f:
        ui = set(re.findall(r"\{action:'(\w+)'", f.read()))
    assert len(ui) >= 14, "действий в интерфейсе подозрительно мало: %s" % sorted(ui)
    assert ui == set(ACTIONS), (
        "интерфейс шлёт %s, таблица знает %s"
        % (sorted(ui - set(ACTIONS)), sorted(set(ACTIONS) - ui)))


def test_masking_helpers_live_in_core_config():
    """Маскирование ключей — в core/aicut/config.py, без подчёркивания и с контрактом.

    Маска — логика безопасности, а не HTTP: наружу уходит «•••xxxx», а присланная
    обратно маска означает «ключ не менял» и подменяется СОХРАНЁННЫМ ключом.
    """
    aicut_config.save_ai_config({"active": "Тест", "profiles": {
        "Тест": {"provider": "openai", "base_url": "http://localhost:1/v1",
                 "api_key": "test-key-9999", "model": "m"}}})
    assert aicut_config.mask_ai_key("") == ""
    assert aicut_config.mask_ai_key("test-key-9999") == "•••9999"
    # env-ключ не маскируется: наружу уходит ИМЯ переменной, а не секрет
    assert aicut_config.mask_ai_key("env:NZ_TEST_VAR") == "env:NZ_TEST_VAR"
    assert aicut_config.masked_profiles({"profiles": {
        "Тест": {"api_key": "test-key-9999"}}})["Тест"]["api_key"] == "•••9999"
    assert aicut_config.unmask_ai_key("•••9999", "Тест") == "test-key-9999"
    assert aicut_config.unmask_ai_key("новый-ключ", "Тест") == "новый-ключ"
    assert aicut_config.unmask_ai_key("•••9999", "НетТакого") == ""
    masked = {"api_key": "•••9999", "base_url": "http://чужой.example/v1"}
    assert aicut_config.saved_profile_for_masked(masked, "Тест") == {
        "provider": "openai", "base_url": "http://localhost:1/v1",
        "api_key": "test-key-9999", "model": "m"}
    assert aicut_config.saved_profile_for_masked({"api_key": "новый"}, "Тест") is None
    assert aicut_config.saved_profile_for_masked(masked, "НетТакого") is None


def test_masking_is_not_implemented_in_api_module():
    """В api/ai.py остались только алиасы на core: реализация масок — не в роуте.

    Копия правил маски в HTTP-модуле — это два ответа на вопрос «что отдаём наружу»
    и второй шанс увести сохранённый ключ на чужой адрес.
    """
    with open(os.path.join(ROOT, "api", "ai.py"), encoding="utf-8") as f:
        src = f.read()
    for name in ("mask_ai_key", "masked_profiles", "unmask_ai_key",
                 "saved_profile_for_masked"):
        assert ("def %s(" % name) not in src, "%s снова реализован в api/ai.py" % name
        assert ("def _%s(" % name) not in src, "_%s снова реализован в api/ai.py" % name
    # Алиасы — для соседних роутов (/api/ai_test, /api/ai_models): это те же объекты,
    # а не вторые определения. mask_ai_key алиаса не имеет: в api/ai.py его никто не звал.
    for alias, real in (("_masked_profiles", "masked_profiles"),
                        ("_unmask_ai_key", "unmask_ai_key"),
                        ("_saved_profile_for_masked", "saved_profile_for_masked")):
        assert getattr(api.ai, alias) is getattr(aicut_config, real), (
            "%s в api/ai.py — не тот же объект, что config.%s" % (alias, real))


def _capture(target):
    """Снять эталон в файл target (прогон вне pytest, см. шапку файла)."""
    cfg_path = os.environ["REELSI_AI_CONFIG"]
    # Модуль api импортирован ещё до подмены REELSI_*: путь к конфигу привязан при
    # импорте, поэтому подменяем его руками (как это делает conftest.py для тестов) и
    # глушим автозасев из личного ai_config.json рабочей копии.
    aicut_config.AI_CONFIG_PATH = cfg_path
    aicut_config._seed_ai_config = lambda: None
    records = run_scenarios(cfg_path)
    with open(target, "w", encoding="utf-8", newline="\r\n") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return records


if __name__ == "__main__":
    try:                        # консоль Windows бывает cp1252/cp866 — не падаем на «->»
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    _target = sys.argv[1] if len(sys.argv) > 1 else FIXTURE
    try:
        _recs = _capture(_target)
        print("сценариев: %d -> %s" % (len(_recs), _target))
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
