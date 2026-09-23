# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Контракты генерации видео (Seedance 2.0 / Veo 3.1 / Hailuo 3 / Kling 3).

Каждый ассерт — реальный отказ провайдера, который стоил ожидания и запроса:
400 «video total duration ... 15.2 in r2v» (видео-референсы длиннее 15с),
видео, поставленное первым кадром (кадр — это КАРТИНКА), длина 5с у Veo (только
4/6/8), кадр вместе с референсами (разные режимы). Всё это ловится до отправки.

Запуск: python -m pytest reelsi/tests -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import aicut  # noqa: E402
from core.umsg import ReelsiError


def _ref(url, role="reference", kind=None, dur=0):
    return {"url": url, "role": role, "kind": kind, "duration": dur, "caption": ""}


@pytest.fixture(autouse=True)
def _isolate_active_video_profile(monkeypatch):
    """Изолируем тесты от ai_config.json на машине: по умолчанию активного профиля нет."""
    monkeypatch.setattr(aicut.video, "_active_video_profile", lambda: None)


# ---- каталог моделей --------------------------------------------------------

def test_builtin_models_cover_main_families():
    """Выпадашка на странице должна давать главные модели БЕЗ похода в сеть."""
    ids = [m["id"] for m in aicut.video_model_list()]
    for need in ("bytedance/seedance-2.0", "google/veo-3.1",
                 "minimax/hailuo-3", "kwaivgi/kling-v3.0-pro"):
        assert need in ids


def test_caps_without_catalog():
    """Каталог провайдера не подтянут — возможности всё равно известны (встроенные)."""
    aicut.video.set_video_catalog(None, {})
    c = aicut.video_caps("google/veo-3.1")
    assert c["durations"] == ["4", "6", "8"]
    assert c["aspect_ratios"] == ["16:9", "9:16"]
    assert c["listed"] is False and c["known"] is True


def test_live_catalog_overrides_builtin():
    """Живой каталог главнее: по нему провайдер и валидирует запрос."""
    aicut.video.set_video_catalog(None, {
        "google/veo-3.1": {"id": "google/veo-3.1", "supported_durations": [8],
                           "supported_resolutions": ["1080p"], "generate_audio": False}})
    try:
        c = aicut.video_caps("google/veo-3.1")
        assert c["durations"] == ["8"] and c["resolutions"] == ["1080p"]
        assert c["audio"] is False and c["listed"] is True
        assert c["ref_images"] == 3          # чего в каталоге нет — берём из встроенного
    finally:
        aicut.video.set_video_catalog(None, {})


def test_unknown_model_is_not_blocked():
    """Незнакомая модель — не наша забота: пусть решает провайдер, а не мы."""
    assert aicut.video_caps("some/new-model") is None
    assert aicut.video_check("some/new-model", {"duration": "99"}, []) == []


# ---- резолвер общего разрешения (ai_config.video_resolution) -----------------

def _res_cfg(monkeypatch, saved):
    monkeypatch.setattr(aicut.video, "load_ai_config",
                        lambda: {"video_resolution": saved, "profiles": {}})


def test_video_resolution_empty_when_not_configured(monkeypatch):
    """Пусто/нет = провайдер решает сам (отправляется "")."""
    _res_cfg(monkeypatch, "")
    assert aicut.video_resolution_cfg("bytedance/seedance-2.0") == ""
    _res_cfg(monkeypatch, "   ")
    assert aicut.video_resolution_cfg("bytedance/seedance-2.0") == ""


def test_video_resolution_supported_known_model(monkeypatch):
    _res_cfg(monkeypatch, "720p")
    assert aicut.video_resolution_cfg("bytedance/seedance-2.0") == "720p"


def test_video_resolution_case_insensitive_canonical(monkeypatch):
    """Сравнение без учёта регистра, наружу — каноничное значение из caps."""
    _res_cfg(monkeypatch, "4k")
    assert aicut.video_resolution_cfg("bytedance/seedance-2.0") == "4K"


def test_video_resolution_stale_known_is_dropped(monkeypatch):
    """Известная модель с caps: не поддерживаемое сохранённое НЕ отправляется."""
    _res_cfg(monkeypatch, "2K")   # seedance поддерживает 480p/720p/1080p/4K, а не 2K
    assert aicut.video_resolution_cfg("bytedance/seedance-2.0") == ""


def test_video_resolution_unknown_model_allowed(monkeypatch):
    """Неизвестная модель (caps нет): непустое сохранённое пропускаем."""
    _res_cfg(monkeypatch, "720p")
    assert aicut.video_resolution_cfg("future/new-model") == "720p"


def test_video_resolution_sync_drops_stale_and_saves(monkeypatch, tmp_path):
    """video_resolution_sync сбрасывает устаревшее значение НА СЕРВЕРЕ."""
    cfg = {"video_resolution": "2K", "profiles": {}}
    monkeypatch.setattr(aicut.video, "load_ai_config", lambda: cfg)
    saved = {}
    monkeypatch.setattr(aicut.video, "save_ai_config", lambda c: saved.update(c))
    assert aicut.video_resolution_sync("bytedance/seedance-2.0") == ""
    assert saved.get("video_resolution") == ""


def test_video_resolution_sync_keeps_supported(monkeypatch):
    cfg = {"video_resolution": "720p", "profiles": {}}
    monkeypatch.setattr(aicut.video, "load_ai_config", lambda: cfg)
    monkeypatch.setattr(aicut.video, "save_ai_config", lambda c: None)
    assert aicut.video_resolution_sync("bytedance/seedance-2.0") == "720p"


# ---- видео-вставки ---------------------------------------------------------

def test_video_prompt_slots_and_empty_profile(tmp_path, monkeypatch):
    """Видео-приписки живут у спикера отдельно от картинок и пустыми не меняют query."""
    from core import speakers
    monkeypatch.setattr(speakers, "SPEAKER_DIR", str(tmp_path))
    speakers.save("Видео", {"video_prompts": {
        "a": {"extra": "cinematic lighting", "pos": "suffix"},
        "b": {"extra": "close-up", "pos": "prefix"},
    }})
    assert aicut.resolve_video_prompt_cfg("a", "Видео") == {
        "extra": "cinematic lighting", "pos": "suffix"}
    assert aicut.build_video_prompt("red bicycle", "a", "Видео") == \
        "red bicycle cinematic lighting"
    assert aicut.build_video_prompt("red bicycle", "b", "Видео") == "close-up red bicycle"
    assert aicut.build_video_prompt("red bicycle", "a", {}) == "red bicycle"


@pytest.mark.parametrize("seconds,model,want", [
    (2.1, "kwaivgi/kling-v3.0-pro", 3),
    (3.0, "kwaivgi/kling-v3.0-pro", 3),
    (3.1, "kwaivgi/kling-v3.0-pro", 4),
    (8.0, "kwaivgi/kling-v3.0-pro", 4),
    (3.0, "google/veo-3.1", 4),
])
def test_video_insert_duration_uses_shortest_supported_length(seconds, model, want):
    assert aicut.video_insert_duration(seconds, model) == want


def test_video_insert_duration_rejects_model_without_short_clip():
    """Hailuo начинается с 5 с: VJOB и оплаченный поток стартовать не должны."""
    with pytest.raises(ReelsiError) as exc:
        aicut.video_insert_duration(3, "minimax/hailuo-3")
    assert "видео-вставки" in str(exc.value)


def test_video_insert_duration_accepts_string_caps_and_unknown_model(monkeypatch):
    """Живой каталог отдаёт строки; неизвестную модель не запрещаем до провайдера."""
    monkeypatch.setattr(aicut.video, "video_caps", lambda _: {"durations": ["3", "4"]})
    assert aicut.video_insert_duration(3.1, "catalog/model") == 4
    monkeypatch.setattr(aicut.video, "video_caps", lambda _: None)
    assert aicut.video_insert_duration(2.1, "future/model") == 3


@pytest.mark.parametrize("w,h,want", [(1920, 1080, "16:9"), (1080, 1920, "9:16"),
                                       (1000, 1000, "1:1")])
def test_video_auto_aspect_chooses_nearest_supported_shape(w, h, want):
    assert aicut.video_auto_aspect(w, h, "bytedance/seedance-2.0") == want


def test_video_auto_shape_uses_first_video_then_image_fallback():
    refs = [{"kind": "image", "w": 1080, "h": 1920},
            {"kind": "video", "duration": 6.1, "w": 1920, "h": 1080},
            {"kind": "video", "duration": 12, "w": 1080, "h": 1920}]
    assert aicut.video_auto_shape(refs, "bytedance/seedance-2.0") == {
        "duration": 7, "aspect_ratio": "16:9", "source": "video"}
    assert aicut.video_auto_shape([refs[0]], "bytedance/seedance-2.0")["aspect_ratio"] == "9:16"
    assert aicut.video_auto_shape([], "bytedance/seedance-2.0")["source"] == "fallback"


@pytest.mark.parametrize("seconds,want", [(1.1, 4), (4.1, 6), (7.1, 8), (99, 8), (None, 4)])
def test_video_auto_duration_clamps_to_model_caps(monkeypatch, seconds, want):
    monkeypatch.setattr(aicut.video, "video_caps",
                        lambda _: {"durations": [4, 6, 8]})
    assert aicut.video_auto_duration(seconds, "test/model") == want


@pytest.mark.parametrize("w,h,want", [(0, 0, "16:9"), (None, 100, "16:9"), (100, 0, "16:9")])
def test_video_auto_aspect_invalid_dimensions_use_first_cap(monkeypatch, w, h, want):
    monkeypatch.setattr(aicut.video, "video_caps",
                        lambda _: {"aspect_ratios": ["16:9", "9:16"]})
    assert aicut.video_auto_aspect(w, h, "test/model") == want


def test_video_auto_aspect_missing_caps_is_empty(monkeypatch):
    monkeypatch.setattr(aicut.video, "video_caps", lambda _: None)
    assert aicut.video_auto_aspect(1920, 1080, "unknown/model") == ""


# ---- предполётные проверки --------------------------------------------------

def test_veo_rejects_alien_duration_and_resolution():
    bad = aicut.video_check("google/veo-3.1", {"duration": "5", "resolution": "480p"}, [])
    assert len(bad) == 2 and "4, 6, 8" in bad[0]


def test_video_as_first_frame_is_caught():
    """Кадр — это КАРТИНКА (frame_images: image_url). Видео туда не принимается."""
    bad = aicut.video_check("bytedance/seedance-2.0", {},
                            [_ref("https://x/a.mp4", "first_frame", "video")])
    assert bad and "только фото" in bad[0]


def test_frames_and_references_are_exclusive():
    """i2v и r2v — разные режимы: OpenRouter молча берёт кадр, ByteDance ругается."""
    bad = aicut.video_check("bytedance/seedance-2.0", {},
                            [_ref("https://x/a.jpg", "first_frame", "image"),
                             _ref("https://x/b.jpg", "reference", "image")])
    assert bad and "разные режимы" in bad[0]


def test_last_frame_without_first():
    bad = aicut.video_check("bytedance/seedance-2.0", {},
                            [_ref("https://x/a.jpg", "last_frame", "image")])
    assert bad and "без первого" in bad[0]


def test_r2v_video_budget():
    """То самое 400 «video total duration ... 15.2»: сумма видео-референсов > 15с."""
    refs = [_ref("https://x/a.mp4", "reference", "video", 12.0),
            _ref("https://x/b.mp4", "reference", "video", 9.0)]
    bad = aicut.video_check("bytedance/seedance-2.0", {}, refs)
    assert bad and "21.0" in bad[0] and "15" in bad[0]
    # уложились — молчим
    refs[1]["duration"] = 2.5
    assert aicut.video_check("bytedance/seedance-2.0", {}, refs) == []


def test_video_reference_only_where_supported():
    """Видео-референс берёт только Seedance; у Kling/Veo это гарантированный отказ."""
    refs = [_ref("https://x/a.mp4", "reference", "video", 4.0)]
    assert aicut.video_check("bytedance/seedance-2.0", {}, refs) == []
    assert aicut.video_check("kwaivgi/kling-v3.0-pro", {}, refs)
    assert aicut.video_check("google/veo-3.1", {}, refs)


def test_audio_reference_is_refused_honestly():
    """@audio1 у Seedance есть, но схемой OpenRouter не передаётся — не молчим."""
    bad = aicut.video_check("bytedance/seedance-2.0", {},
                            [_ref("https://x/a.mp3", "reference", "audio", 3.0)])
    assert bad and "аудио" in bad[0].lower()


def test_page_link_instead_of_file():
    """Ссылка на страницу галереи вместо файла — провайдер качает URL сам и подавится."""
    bad = aicut.video_check("bytedance/seedance-2.0", {},
                            [_ref("https://site/gallery/123", "reference", "page")],
                            prompt="x")
    assert bad and "страниц" in bad[0]


def test_non_https_ref_stops_generation():
    """Раньше такой референс молча выбрасывался, а генерация шла — юзер платил за
    ролик без своего референса."""
    bad = aicut.video_check("bytedance/seedance-2.0", {},
                            [_ref("http://h/a.jpg", "reference", "image")], prompt="x")
    assert bad and "https" in bad[0]


def test_prompt_is_required():
    bad = aicut.video_check("bytedance/seedance-2.0", {},
                            [_ref("https://h/a.jpg", "reference", "image")], prompt="   ")
    assert bad and "текст запроса" in bad[0]


def test_non_video_model_is_stopped_when_catalog_known():
    """В профиле у юзера лежала несуществующая модель — запрос уходил в никуда."""
    aicut.video.set_video_catalog(None, {"bytedance/seedance-2.0": {"id": "bytedance/seedance-2.0"}})
    try:
        bad = aicut.video_check("google/gemini-2.5-flash", {}, [], prompt="x")
        assert bad and "не видео-модель" in bad[0]
    finally:
        aicut.video.set_video_catalog(None, {})


def test_dangling_tag_is_noticed():
    """@video1 в промпте без приложенного видео модель ни с чем не свяжет."""
    refs = [_ref("https://h/a.jpg", "reference", "image")]
    assert aicut.dangling_tags("камера как в @video1, лицо с @image1", refs) == ["@video1"]
    assert aicut.dangling_tags("лицо с @image1", refs) == []


def test_two_last_frames():
    bad = aicut.video_check("bytedance/seedance-2.0", {},
                            [_ref("https://h/a.jpg", "first_frame", "image"),
                             _ref("https://h/b.jpg", "last_frame", "image"),
                             _ref("https://h/c.jpg", "last_frame", "image")], prompt="x")
    assert bad and "только один" in bad[0]


def test_webp_blocked_for_veo_allowed_for_seedance():
    """Veo не принимает .webp (поймано пользователем), Seedance принимает."""
    ref = [_ref("https://h/a.webp", "first_frame", "image")]
    bad = aicut.video_check("google/veo-3.1", {}, ref, prompt="x")
    assert bad and "webp" in bad[0]
    assert aicut.video_check("bytedance/seedance-2.0", {}, ref, prompt="x") == []


def test_uncertain_format_warns_but_does_not_block():
    """У Kling/Hailuo данных меньше — ложный запрет хуже предупреждения."""
    ref = [_ref("https://h/a.webp", "first_frame", "image")]
    assert aicut.video_check("kwaivgi/kling-v3.0-pro", {}, ref, prompt="x") == []
    assert any("webp" in w for w in aicut.video_warnings("kwaivgi/kling-v3.0-pro", ref))


def test_image_format_from_probe_not_extension():
    """У ссылки может не быть расширения — формат берём от ffprobe/Content-Type."""
    assert aicut.image_format_of({"url": "https://h/x?id=1", "codec": "webp"}) == "webp"
    assert aicut.image_format_of({"url": "https://h/x", "ctype": "image/png"}) == "png"
    assert aicut.image_format_of({"url": "https://h/a.JPG"}) == "jpeg"
    assert aicut.image_format_of({"url": "https://h/x"}) == ""


def test_ref_counts():
    imgs = [_ref("https://x/%d.jpg" % i, "reference", "image") for i in range(10)]
    assert aicut.video_check("bytedance/seedance-2.0", {}, imgs)      # 10 > 9
    assert aicut.video_check("bytedance/seedance-2.0", {}, imgs[:9]) == []


# ---- разбор ответа провайдера ----------------------------------------------

def test_provider_error_is_unwrapped_and_translated():
    """OpenRouter вкладывает ошибку апстрима СТРОКОЙ в свой JSON — в UI летела каша."""
    inner = json.dumps({"error": {"code": "InvalidParameter", "message":
                        "the parameter video total duration (seconds) specified in the "
                        "request must be less than or equal to 15.2 for model "
                        "dreamina-seedance-2-0 in r2v. Request id: 0217859"}})
    msg, hint = aicut._video_error_text(json.dumps({"error": {"message": "HTTP 400: " + inner}}))
    assert "Request id" not in msg
    assert "15с" in hint and "референс" in hint


def test_https_only_error_is_translated():
    msg, hint = aicut._video_error_text('{"error":{"message":"Only HTTPS URLs are allowed"}}')
    assert "https" in hint.lower()


# ---- раскладка референсов по полям запроса ----------------------------------

def test_kind_wins_over_extension():
    """Тип берём у ffprobe: у ссылок расширения часто нет вовсе."""
    assert aicut._ref_is_video({"url": "https://x/abc?id=1", "kind": "video"})
    assert not aicut._ref_is_video({"url": "https://x/a.mp4", "kind": "image"})
    assert aicut._ref_is_video({"url": "https://x/a.mp4"})       # kind нет — по расширению


def test_ref_tags_numbered_by_type():
    refs = [_ref("https://x/a.mp4", kind="video"), _ref("https://x/b.jpg", kind="image"),
            _ref("https://x/c.mp4", kind="video")]
    assert aicut.video_ref_tags(refs) == ["@video1", "@image1", "@video2"]


@pytest.mark.parametrize("text,want", [
    ("камера как в @видео", "@video1"),
    ("лицо с @photo2", "@image2"),
    ("ритм из @audio", "@audio1"),
])
def test_user_tags_normalized(text, want):
    assert want in aicut.normalize_video_tags(text)


# ---- история задач (что сервер посчитал и что ещё можно скачать) -------------
# Страницу за время генерации закрывают и жмут F5, поэтому список задач живёт на
# сервере. Тесты стерегут ровно то, из-за чего он был бы бесполезен: пропавшие
# после рестарта задачи, невидимые старые ролики и «убрать», которое не убирает.

os.environ.setdefault("REELSI_NO_BROWSER", "1")


@pytest.fixture()
def vapi(tmp_path, monkeypatch):
    """API с историей и папкой роликов во временном каталоге (боевые не трогаем)."""
    import api
    import webui
    out = tmp_path / "out"
    out.mkdir()
    # Подменяем api.videogen, а НЕ api: бэкенд — пакет, и функции истории читают эти
    # пути из globals своего модуля. Подмена на фасаде им не видна, и тест писал бы
    # в боевую папку роликов.
    monkeypatch.setattr(api.videogen, "VIDEO_OUT", str(out))
    monkeypatch.setattr(api.videogen, "VIDEO_HIST_PATH", str(tmp_path / "history.json"))
    # /api/video_gen теперь тянет каталог провайдера сам, до нормализации. Тестам
    # предполёта сеть не нужна — сетевой GET заставил бы их ждать/фейлиться.
    monkeypatch.setattr(aicut, "ensure_video_catalog", lambda *a, **k: None)
    return api, webui.app.test_client(), out


def test_interrupted_task_is_marked_not_running(vapi):
    """Рестарт сервера убивает поток генерации — запись «идёт» осталась бы вечной."""
    api, client, _ = vapi
    api.vhist_put("v1", ts=1, status="running", prompt="кот")
    api.vhist_boot()
    it = client.get("/api/video_history").get_json()["items"][0]
    assert it["status"] == "lost" and "перезапуск" in it["error"]


def test_old_files_show_up_in_the_list(vapi):
    """Ролики, сгенерированные до появления истории, — тоже «что можно скачать»."""
    api, client, out = vapi
    (out / "seedance_abc.mp4").write_bytes(b"\0" * 2048)
    d = client.get("/api/video_history").get_json()
    it = d["items"][0]
    assert it["status"] == "done" and it["exists"] and it["size"] == 2048
    assert it["name"] == "seedance_abc.mp4" and it["url"].startswith("/api/media?path=")


def test_missing_file_is_not_offered_for_download(vapi):
    """Файл убрали руками — честное «файла нет», а не 404 по кнопке «Скачать»."""
    api, client, out = vapi
    api.vhist_put("v1", ts=1, status="done", path=str(out / "gone.mp4"))
    it = client.get("/api/video_history").get_json()["items"][0]
    assert it["exists"] is False and it["url"] == ""


def test_delete_takes_the_file_with_it(vapi):
    """Иначе файл вернулся бы в список следующим же обходом папки."""
    api, client, out = vapi
    p = out / "seedance_abc.mp4"
    p.write_bytes(b"\0")
    key = client.get("/api/video_history").get_json()["items"][0]["key"]
    assert client.post("/api/video_history",
                       json={"action": "delete", "key": key}).get_json()["removed"] == 1
    assert not p.exists()
    assert client.get("/api/video_history").get_json()["items"] == []


def test_running_task_is_not_deleted_under_the_worker(vapi):
    """Удалить идущую задачу нельзя: поток дописал бы в неё результат после удаления."""
    api, client, _ = vapi
    api.vhist_put("v9", ts=1, status="running", prompt="кот")
    api.VJOB.update(running=True, key="v9")
    try:
        d = client.post("/api/video_history", json={"action": "delete", "key": "v9"}).get_json()
        assert "error" in d
    finally:
        api.VJOB.update(running=False, key=None)


def test_provider_task_id_is_kept_from_the_log(vapi):
    """Единственный след оплаченной задачи, если сервер убьют посреди генерации."""
    api, client, _ = vapi
    api.vhist_put("v1", ts=1, status="running")
    api.VJOB.update(key="v1", log=[], log_base=0)
    try:
        api.vemit("  видео: задача {task} принята, жду готовности…", task="gen_777")
    finally:
        api.VJOB.update(key=None)
    it = client.get("/api/video_history").get_json()["items"][0]
    assert it["task"] == "gen_777"


def test_vemit_formats_structured_video_log(vapi):
    """gen_video пишет emit(..., model=...); VJOB обязан сохранить готовую строку."""
    api, _, _ = vapi
    api.VJOB.update(key=None, log=[], log_base=0)
    api.vemit("  видео: {model} — отправляю запрос", model="google/veo-3.1")
    assert api.VJOB["log"] == ["  видео: google/veo-3.1 — отправляю запрос"]


def test_video_status_exposes_job_key_and_context(vapi):
    """F5 связывает running/done VJOB с insert-карточкой без текущего индекса."""
    api, client, _ = vapi
    api.VJOB.update(running=True, done=False, key="v_context", context="iv_card", log=[], log_base=0)
    try:
        d = client.get("/api/video_status").get_json()
        assert d["key"] == "v_context" and d["context"] == "iv_card"
    finally:
        api.VJOB.update(running=False, done=False, key=None, context="")


def test_video_cancel_sets_worker_flag(vapi):
    api, client, _ = vapi
    api.VJOB.update(cancel=False)
    d = client.post("/api/video_cancel").get_json()
    assert d["ok"] is True and api.VJOB["cancel"] is True
    api.VJOB.update(cancel=False)


class _NoVideoThread:
    """Не запускаем worker: API-тест проверяет предполёт без сети и оплаты."""
    made = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.__class__.made.append(self)

    def start(self):
        pass


def test_video_insert_api_calculates_prompt_and_duration_before_worker(vapi, monkeypatch):
    """Карточка не присылает финальный prompt/duration и не меняет свой duration_sec."""
    api, client, out = vapi
    xml = out.parent / "clip.xml"
    xml.write_text("<xmeml><sequence><duration>240</duration><rate><timebase>60</timebase></rate>"
                   "<media><video><format><samplecharacteristics><width>1080</width>"
                   "<height>1920</height></samplecharacteristics></format></video></media>"
                   "</sequence></xmeml>", encoding="utf-8")
    from core import speakers
    tmp_speakers = os.path.join(os.path.dirname(api.videogen.VIDEO_HIST_PATH), "speakers")
    monkeypatch.setattr(speakers, "SPEAKER_DIR", tmp_speakers)
    speakers.save("Видео", {"video_prompts": {"a": {"extra": "slow dolly", "pos": "prefix"}}})
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: {
        "name": "test", "provider": "openrouter", "base_url": "https://example.test", "api_key": ""})
    monkeypatch.setattr(aicut, "video_model_cfg", lambda: "google/veo-3.1")
    monkeypatch.setattr(aicut.video, "load_ai_config",
                        lambda: {"video_resolution": "", "profiles": {}})
    _NoVideoThread.made = []
    monkeypatch.setattr(api.videogen.threading, "Thread", _NoVideoThread)
    api.VJOB.update(running=False, done=False, cancel=False, log=[], log_base=0,
                    result=None, error=None, err=None, err_vars=None, started=0, key=None)
    try:
        d = client.post("/api/video_gen", json={"query": "red bicycle", "slot": "a",
                                                  "speaker": "Видео", "insert_duration": 3.1,
                                                  "xml": str(xml)},
                        headers={"X-Reelsi-Video-Context": "iv_original_card"}).get_json()
        assert d["ok"] and d["prompt"] == "slow dolly red bicycle"
        assert d["duration"] == 4 and d["aspect_ratio"] == "9:16" and d["model"] == "google/veo-3.1"
        assert d["resolution"] == ""
        assert len(_NoVideoThread.made) == 1
        it = client.get("/api/video_history").get_json()["items"][0]
        assert it["model"] == d["model"] and it["prompt"] == d["prompt"]
        assert it["opts"]["duration"] == d["duration"] == 4
        assert it["opts"]["aspect_ratio"] == "9:16"
        status = client.get("/api/video_status").get_json()
        assert status["key"] == d["key"] and status["context"] == "iv_original_card"
    finally:
        api.VJOB.update(running=False, done=False, cancel=False, key=None)


def test_video_insert_api_rejects_five_second_model_before_worker(vapi, monkeypatch):
    api, client, _ = vapi
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: {
        "name": "test", "provider": "openrouter", "base_url": "https://example.test", "api_key": ""})
    monkeypatch.setattr(aicut, "video_model_cfg", lambda: "minimax/hailuo-3")
    _NoVideoThread.made = []
    monkeypatch.setattr(api.videogen.threading, "Thread", _NoVideoThread)
    api.VJOB.update(running=False, done=False, cancel=False, key=None)
    d = client.post("/api/video_gen", json={"query": "red bicycle", "slot": "a",
                                              "insert_duration": 3}).get_json()
    assert d["err"] == "video_insert_duration"
    assert not _NoVideoThread.made and not api.VJOB["running"]


def test_raw_video_api_computes_shape_from_probe_refs(vapi, monkeypatch):
    """Raw получает duration/aspect из probe-метаданных, а модель — из config."""
    api, client, _ = vapi
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: {
        "name": "test", "provider": "openrouter", "base_url": "https://example.test", "api_key": ""})
    monkeypatch.setattr(aicut, "video_model_cfg", lambda: "bytedance/seedance-2.0")
    monkeypatch.setattr(aicut.video, "load_ai_config",
                        lambda: {"video_resolution": "", "profiles": {}})
    _NoVideoThread.made = []
    monkeypatch.setattr(api.videogen.threading, "Thread", _NoVideoThread)
    api.VJOB.update(running=False, done=False, cancel=False, log=[], log_base=0,
                    result=None, error=None, err=None, err_vars=None, started=0, key=None,
                    context="")
    try:
        d = client.post("/api/video_gen", json={
            "prompt": "neon bicycle", "model": "minimax/hailuo-3", "duration": "4",
            "resolution": "720p", "aspect_ratio": "9:16", "seed": "17", "audio": True,
            "refs": [{"url": "https://example.test/source.mp4", "kind": "video",
                      "duration": 7.1, "w": 1920, "h": 1080}]}).get_json()
        assert d["ok"] and d["model"] == "bytedance/seedance-2.0"
        assert d["prompt"] == "neon bicycle" and d["duration"] == 8
        assert d["aspect_ratio"] == "16:9"
        assert len(_NoVideoThread.made) == 1
        it = client.get("/api/video_history").get_json()["items"][0]
        assert it["model"] == "bytedance/seedance-2.0" and it["prompt"] == "neon bicycle"
        # разрешение/модель/длительность/аспект из body ИГНОРИРУЮТСЯ: сервер считает сам
        assert it["opts"] == {"model": "bytedance/seedance-2.0", "duration": 8,
                              "resolution": "", "aspect_ratio": "16:9", "size": None,
                              "seed": "17", "audio": True}
    finally:
        api.VJOB.update(running=False, done=False, cancel=False, key=None, context="")


def test_raw_video_api_ignores_body_resolution(vapi, monkeypatch):
    """Raw берёт разрешение из ГЛОБАЛЬНОЙ настройки, а не из body."""
    api, client, _ = vapi
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: {
        "name": "test", "provider": "openrouter", "base_url": "https://example.test", "api_key": ""})
    monkeypatch.setattr(aicut, "video_model_cfg", lambda: "bytedance/seedance-2.0")
    monkeypatch.setattr(aicut.video, "load_ai_config",
                        lambda: {"video_resolution": "1080p", "profiles": {}})
    _NoVideoThread.made = []
    monkeypatch.setattr(api.videogen.threading, "Thread", _NoVideoThread)
    api.VJOB.update(running=False, done=False, cancel=False, log=[], log_base=0,
                    result=None, error=None, err=None, err_vars=None, started=0, key=None,
                    context="")
    try:
        d = client.post("/api/video_gen", json={
            "prompt": "neon bicycle", "resolution": "720p", "duration": "4",
            "aspect_ratio": "9:16",
            "refs": [{"url": "https://example.test/source.mp4", "kind": "video",
                      "duration": 7.1, "w": 1920, "h": 1080}]}).get_json()
        assert d["ok"]
        it = client.get("/api/video_history").get_json()["items"][0]
        assert it["opts"]["resolution"] == "1080p", (
            "body resolution=720p протёк в opts — разрешение обязано быть из ai_config")
    finally:
        api.VJOB.update(running=False, done=False, cancel=False, key=None, context="")


def test_video_resolution_stale_not_sent(vapi, monkeypatch):
    """Устаревшее разрешение (модель его не поддерживает) провайдеру НЕ уходит."""
    api, client, _ = vapi
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: {
        "name": "test", "provider": "openrouter", "base_url": "https://example.test", "api_key": ""})
    monkeypatch.setattr(aicut, "video_model_cfg", lambda: "bytedance/seedance-2.0")
    monkeypatch.setattr(aicut.video, "load_ai_config",
                        lambda: {"video_resolution": "2K", "profiles": {}})
    _NoVideoThread.made = []
    monkeypatch.setattr(api.videogen.threading, "Thread", _NoVideoThread)
    api.VJOB.update(running=False, done=False, cancel=False, log=[], log_base=0,
                    result=None, error=None, err=None, err_vars=None, started=0, key=None,
                    context="")
    try:
        d = client.post("/api/video_gen", json={"prompt": "neon bicycle"}).get_json()
        assert d["ok"] and d["resolution"] == ""
        it = client.get("/api/video_history").get_json()["items"][0]
        assert it["opts"]["resolution"] == ""
    finally:
        api.VJOB.update(running=False, done=False, cancel=False, key=None, context="")


def test_video_insert_uses_global_resolution_and_portrait_aspect(vapi, monkeypatch):
    """Insert portrait XML + глобальное 720p => model, duration, 720p, 9:16."""
    api, client, out = vapi
    xml = out.parent / "clip.xml"
    xml.write_text("<xmeml><sequence><duration>240</duration><rate><timebase>60</timebase></rate>"
                   "<media><video><format><samplecharacteristics><width>1080</width>"
                   "<height>1920</height></samplecharacteristics></format></video></media>"
                   "</sequence></xmeml>", encoding="utf-8")
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: {
        "name": "test", "provider": "openrouter", "base_url": "https://example.test", "api_key": ""})
    monkeypatch.setattr(aicut, "video_model_cfg", lambda: "bytedance/seedance-2.0")
    monkeypatch.setattr(aicut.video, "load_ai_config",
                        lambda: {"video_resolution": "720p", "profiles": {}})
    _NoVideoThread.made = []
    monkeypatch.setattr(api.videogen.threading, "Thread", _NoVideoThread)
    api.VJOB.update(running=False, done=False, cancel=False, log=[], log_base=0,
                    result=None, error=None, err=None, err_vars=None, started=0, key=None,
                    context="")
    try:
        d = client.post("/api/video_gen", json={"query": "red bicycle", "slot": "a",
                                                  "insert_duration": 3.1,
                                                  "xml": str(xml)}).get_json()
        assert d["ok"] and d["model"] == "bytedance/seedance-2.0"
        assert d["duration"] == 4 and d["aspect_ratio"] == "9:16"
        assert d["resolution"] == "720p"
        it = client.get("/api/video_history").get_json()["items"][0]
        assert it["opts"]["model"] == "bytedance/seedance-2.0"
        assert it["opts"]["duration"] == 4
        assert it["opts"]["resolution"] == "720p"
        assert it["opts"]["aspect_ratio"] == "9:16"
    finally:
        api.VJOB.update(running=False, done=False, cancel=False, key=None, context="")


_LIVE_MODEL = "acme/videogen-live"
_LIVE_CAPS = {
    "id": _LIVE_MODEL,
    "supported_durations": list(range(4, 16)),
    "supported_resolutions": ["1080p"],
    "supported_aspect_ratios": ["9:16"],
}


def _live_ensure(called):
    """ensure_video_catalog, который «дотянул» каталог: кладёт live-only модель с
    supported_durations 4..15 и подходящими aspect/resolution caps, как свежий процесс
    после GET {base}/videos/models."""
    def ensure(prof, emit=None):
        called.append(prof)
        aicut.video.set_video_catalog(prof, {_LIVE_MODEL: _LIVE_CAPS})
    return ensure


def test_video_insert_live_only_model_uses_early_catalog(vapi, monkeypatch):
    """Регресс: свежий процесс + live-only модель (нет во встроенном каталоге) —
    ранний ensure_video_catalog подгружает её caps ДО нормализации, и длина 3
    округляется по supported_durations до 4, а не уезжает в worker уже выбранной
    недопустимой 3 (то самое «длину 3 модель не принимает — можно: 4...15»)."""
    api, client, out = vapi
    xml = out.parent / "clip.xml"
    xml.write_text("<xmeml><sequence><duration>240</duration><rate><timebase>60</timebase></rate>"
                   "<media><video><format><samplecharacteristics><width>1080</width>"
                   "<height>1920</height></samplecharacteristics></format></video></media>"
                   "</sequence></xmeml>", encoding="utf-8")
    called = []
    monkeypatch.setattr(aicut, "ensure_video_catalog", _live_ensure(called))
    prof = {
        "name": "test", "provider": "openrouter", "base_url": "https://example.test", "api_key": ""}
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: prof)
    monkeypatch.setattr(aicut.video, "_active_video_profile", lambda: prof)
    monkeypatch.setattr(aicut, "video_model_cfg", lambda: _LIVE_MODEL)
    monkeypatch.setattr(aicut.video, "load_ai_config",
                        lambda: {"video_resolution": "1080p", "profiles": {}})
    _NoVideoThread.made = []
    monkeypatch.setattr(api.videogen.threading, "Thread", _NoVideoThread)
    api.VJOB.update(running=False, done=False, cancel=False, log=[], log_base=0,
                    result=None, error=None, err=None, err_vars=None, started=0, key=None,
                    context="")
    try:
        d = client.post("/api/video_gen", json={"query": "red bicycle", "slot": "a",
                                                  "insert_duration": 3,
                                                  "xml": str(xml)}).get_json()
        assert d["ok"] and d["model"] == _LIVE_MODEL
        assert d["duration"] == 4
        assert d["resolution"] == "1080p" and d["aspect_ratio"] == "9:16"
        assert len(_NoVideoThread.made) == 1
        it = client.get("/api/video_history").get_json()["items"][0]
        assert it["opts"]["duration"] == 4
        assert it["opts"]["resolution"] == "1080p" and it["opts"]["aspect_ratio"] == "9:16"
        assert len(called) == 1 and called[0]["name"] == "test"
    finally:
        api.VJOB.update(running=False, done=False, cancel=False, key=None, context="")
        aicut.video.set_video_catalog(None, {})


def test_raw_video_uses_early_catalog_for_model_dependent_params(vapi, monkeypatch):
    """Регресс raw: duration/aspect/resolution тоже считаются по caps live-модели из
    раннего каталога (6.1 -> 7, портрет 9:16, 1080p), а не по встроенному."""
    api, client, _ = vapi
    called = []
    monkeypatch.setattr(aicut, "ensure_video_catalog", _live_ensure(called))
    prof = {
        "name": "test", "provider": "openrouter", "base_url": "https://example.test", "api_key": ""}
    monkeypatch.setattr(aicut, "resolve_video_profile", lambda: prof)
    monkeypatch.setattr(aicut.video, "_active_video_profile", lambda: prof)
    monkeypatch.setattr(aicut, "video_model_cfg", lambda: _LIVE_MODEL)
    monkeypatch.setattr(aicut.video, "load_ai_config",
                        lambda: {"video_resolution": "1080p", "profiles": {}})
    _NoVideoThread.made = []
    monkeypatch.setattr(api.videogen.threading, "Thread", _NoVideoThread)
    api.VJOB.update(running=False, done=False, cancel=False, log=[], log_base=0,
                    result=None, error=None, err=None, err_vars=None, started=0, key=None,
                    context="")
    try:
        d = client.post("/api/video_gen", json={
            "prompt": "neon bicycle",
            "refs": [{"url": "https://example.test/source.mp4", "kind": "video",
                      "duration": 6.1, "w": 1080, "h": 1920}]}).get_json()
        assert d["ok"] and d["duration"] == 7
        assert d["aspect_ratio"] == "9:16" and d["resolution"] == "1080p"
        assert len(_NoVideoThread.made) == 1
        it = client.get("/api/video_history").get_json()["items"][0]
        assert it["opts"]["duration"] == 7
        assert it["opts"]["resolution"] == "1080p" and it["opts"]["aspect_ratio"] == "9:16"
        assert len(called) == 1
    finally:
        api.VJOB.update(running=False, done=False, cancel=False, key=None, context="")
        aicut.video.set_video_catalog(None, {})
