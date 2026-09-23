# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Кэши и порядок папок камер — три места, где ошибка НЕ ВИДНА сразу: юзер просто
получает устаревший результат или перепутанные камеры и не понимает почему.

Все три бага задокументированы в ARCHITECTURE.md как реальные:
- `find_cam_dirs` сортирует по ЧИСЛУ в имени, иначе регистр меняет камеры местами;
- ключ кэша транскрипта включает модель, иначе medium-кэш выдаётся за large-v3;
- ключ маски рото включает делитель разрешения и модель RVM, иначе реюзаются
  маски от другой версии.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import cams
from core import roto
from core import transcribe
from core import app_meta


# --------------------------------------------------------------------------- #
# папка результата: переименование AutoCut -> Reelsi не должно «терять» проекты
# --------------------------------------------------------------------------- #
def test_out_dir_prefers_existing_legacy_folder(tmp_path):
    """На диске лежит AutoCut_out с готовыми проектами — работаем в ней.

    Иначе переименование проекта показало бы юзеру пустую папку, а все его
    .project.json перестали бы находиться."""
    base = str(tmp_path)
    os.makedirs(os.path.join(base, "AutoCut_out"))
    assert app_meta.out_dir(base) == os.path.join(base, "AutoCut_out")


def test_out_dir_new_install_uses_new_name(tmp_path):
    """Чистая установка — заводим Reelsi_out, а не старое имя."""
    assert app_meta.out_dir(str(tmp_path)) == os.path.join(str(tmp_path), "Reelsi_out")


def test_out_dir_new_name_wins_when_both_exist(tmp_path):
    """Обе папки есть — новая приоритетнее: значит юзер уже переехал."""
    base = str(tmp_path)
    os.makedirs(os.path.join(base, "AutoCut_out"))
    os.makedirs(os.path.join(base, "Reelsi_out"))
    assert app_meta.out_dir(base) == os.path.join(base, "Reelsi_out")


def test_is_out_dir_accepts_both_names():
    """xml2ae определяет по имени папки, где корень проекта — оба имени рабочие."""
    assert app_meta.is_out_dir("AutoCut_out")
    assert app_meta.is_out_dir("reelsi_out")      # регистр не важен
    assert not app_meta.is_out_dir("камера1")
    assert not app_meta.is_out_dir(None)


def test_env_falls_back_to_old_prefix(monkeypatch):
    """Старые AUTOCUT_*-переменные из launch.json юзера обязаны ещё работать,
    но новые REELSI_* важнее."""
    monkeypatch.delenv("REELSI_UI_STATE", raising=False)
    monkeypatch.setenv("AUTOCUT_UI_STATE", "old.json")
    assert app_meta.env("UI_STATE") == "old.json"
    monkeypatch.setenv("REELSI_UI_STATE", "new.json")
    assert app_meta.env("UI_STATE") == "new.json"


# --------------------------------------------------------------------------- #
# find_cam_dirs: порядок по числу, а не по регистру
# --------------------------------------------------------------------------- #
def _mkdirs(base, *names):
    for n in names:
        os.makedirs(os.path.join(base, n), exist_ok=True)
    return str(base)


def test_cam_dirs_sorted_by_number_not_case(tmp_path):
    """«Камера2» (заглавная К) не должна опережать «камера1»: обычная сортировка
    строк ставит её первой, и весь монтаж поехал бы за второй камерой."""
    base = _mkdirs(tmp_path, "Камера2", "камера1")
    assert [os.path.basename(p) for p in cams.find_cam_dirs(base)] == ["камера1", "Камера2"]


def test_cam_dirs_number_beats_lexicographic(tmp_path):
    """камера10 идёт ПОСЛЕ камера2 — сравниваем числа, а не строки."""
    base = _mkdirs(tmp_path, "камера10", "камера2", "камера1")
    assert [os.path.basename(p) for p in cams.find_cam_dirs(base)] == [
        "камера1", "камера2", "камера10"]


def test_cam_dirs_ignore_foreign_folders(tmp_path):
    """Посторонние папки проекта в камеры не попадают."""
    base = _mkdirs(tmp_path, "камера1", "music", "AutoCut_out", "roto")
    assert [os.path.basename(p) for p in cams.find_cam_dirs(base)] == ["камера1"]


# --------------------------------------------------------------------------- #
# кэш транскрипта: ключ включает модель/движок/язык
# --------------------------------------------------------------------------- #
def test_words_cache_key_separates_models():
    """Прогон с --model medium не должен отдаваться при следующем запуске как
    large-v3 (и наоборот — subtitle_xml не должен подхватывать чужой кэш)."""
    src = r"C:\footage\cam1\C1234.MP4"
    large = transcribe.words_cache_path(src, "large-v3")
    assert large != transcribe.words_cache_path(src, "medium")
    assert large != transcribe.words_cache_path(src, "large-v3", engine="gigaam")
    assert large != transcribe.words_cache_path(src, "large-v3", lang="en")
    assert large == transcribe.words_cache_path(src, "large-v3")   # детерминирован
    assert large.endswith(".json") and "C1234" in os.path.basename(large)


def test_words_cache_roundtrip(tmp_path):
    words = [{"w": "привет", "start": 0.1, "end": 0.5}]
    p = str(tmp_path / "a.words.json")
    transcribe.save_words_cache(p, words)
    assert transcribe.load_words_cache(p) == words
    assert not os.path.exists(p + ".tmp")           # атомарная запись прибирает за собой


def test_broken_cache_is_dropped_not_raised(tmp_path):
    """Обрезанный JSON (закрыли процесс во время записи) раньше ронял КАЖДЫЙ
    следующий запуск JSONDecodeError-ом, не подсказывая, что файл надо удалить."""
    p = str(tmp_path / "a.words.json")
    open(p, "w", encoding="utf-8").write('[{"w":"обрыв", "star')
    assert transcribe.load_words_cache(p) is None
    assert not os.path.exists(p)                    # сам себя вылечил


def test_empty_transcript_is_not_cached(tmp_path):
    """Тишина в записи не должна навсегда зафиксировать «0 слов»."""
    p = str(tmp_path / "a.words.json")
    transcribe.save_words_cache(p, [])
    assert not os.path.exists(p)
    import json
    json.dump([], open(p, "w", encoding="utf-8"))
    assert transcribe.load_words_cache(p) is None


# --------------------------------------------------------------------------- #
# кэш масок рото: что должно ломать ключ, а что — нет
# --------------------------------------------------------------------------- #
def test_mask_key_tracks_everything_that_changes_the_mask(tmp_path, monkeypatch):
    """Цена ошибки высокая: реюз чужой маски виден только в AE. Ключ обязан
    меняться от границ, низа, делителя разрешения и версии/качества RVM."""
    vid = tmp_path / "cam1.mp4"
    vid.write_bytes(b"x")
    v = str(vid)
    base = roto._mask_key(v, 0.0, 2.5, 0.35, 2)

    assert base == roto._mask_key(v, 0.0, 2.5, 0.35, 2)      # стабилен
    assert base != roto._mask_key(v, 0.1, 2.5, 0.35, 2)      # начало
    assert base != roto._mask_key(v, 0.0, 2.6, 0.35, 2)      # конец
    assert base != roto._mask_key(v, 0.0, 2.5, 0.40, 2)      # низ кадра
    assert base != roto._mask_key(v, 0.0, 2.5, 0.35, 1)      # v1-маски не реюзятся

    monkeypatch.setattr(roto, "_VARIANT", "resnet50")
    assert base != roto._mask_key(v, 0.0, 2.5, 0.35, 2)      # смена модели RVM


def test_mask_key_follows_source_mtime(tmp_path):
    """Перезаписали исходник — старая маска не подходит."""
    vid = tmp_path / "cam1.mp4"
    vid.write_bytes(b"x")
    v = str(vid)
    before = roto._mask_key(v, 0.0, 2.5, 0.35, 2)
    os.utime(v, (0, 0))
    assert before != roto._mask_key(v, 0.0, 2.5, 0.35, 2)


# --------------------------------------------------------------------------- #
# очистка _tmp: превью-прокси — кэш, а не мусор
# --------------------------------------------------------------------------- #
def _mk_tmp(root):
    """<root>/_tmp с прокси и обычным хламом черновика."""
    from core import draftrender
    t = draftrender.tmp_dir(str(root))
    for name, size in (("pv_abc123.mp4", 3000), ("pv_def456.mp4", 2000),
                       ("01.draft_filters.txt", 100), ("01.draft.ass", 50)):
        with open(os.path.join(t, name), "wb") as f:
            f.write(b"\0" * size)
    os.makedirs(os.path.join(t, "sub"), exist_ok=True)
    with open(os.path.join(t, "sub", "junk.bin"), "wb") as f:
        f.write(b"\0" * 10)
    return t


def test_clean_tmp_keeps_preview_proxies_by_default(tmp_path):
    """Авто-очистка перед нарезкой прокси НЕ трогает.

    Она зовётся на КАЖДУЮ нарезку в папке результата (api._run_cut_job). Снеси она
    прокси — каждая новая нарезка обнуляла бы предпросмотр всем клипам папки разом,
    а пересборка стоит десятки секунд на файл камеры. Прокси зависят от исходника,
    не от монтажа, поэтому переживать нарезку обязаны.
    """
    from core import draftrender
    t = _mk_tmp(tmp_path)
    freed = draftrender.clean_tmp(str(tmp_path), emit=lambda *a: None)
    assert os.path.isfile(os.path.join(t, "pv_abc123.mp4"))
    assert os.path.isfile(os.path.join(t, "pv_def456.mp4"))
    assert not os.path.exists(os.path.join(t, "01.draft_filters.txt"))
    assert not os.path.exists(os.path.join(t, "sub", "junk.bin"))
    assert freed == 160, "в освобождённое попали прокси, хотя их оставили"


def test_clean_tmp_drops_proxies_when_asked(tmp_path):
    """Явная уборка кнопкой сносит и прокси — место бывает нужно прямо сейчас."""
    from core import draftrender
    t = _mk_tmp(tmp_path)
    freed = draftrender.clean_tmp(str(tmp_path), emit=lambda *a: None, proxies=True)
    assert not os.path.exists(t), "_tmp должен уйти целиком"
    assert freed == 5160


def test_proxy_size_counts_only_proxies(tmp_path):
    """Размер прокси считается отдельно — кнопка очистки показывает цену вопроса."""
    from core import draftrender
    _mk_tmp(tmp_path)
    assert draftrender.proxy_size(str(tmp_path)) == 5000


# --------------------------------------------------------------------------- #
# ключ прокси: повёрнутые исходники не должны переиспользовать до-фиксовый кэш
# --------------------------------------------------------------------------- #
def _fake_src(tmp_path, name="cam.mp4"):
    p = tmp_path / name
    p.write_bytes(b"\0" * 1024)
    return str(p)


def test_rotated_source_gets_its_own_proxy_key(tmp_path, monkeypatch):
    """У повёрнутого исходника ключ кэша ДРУГОЙ, чем у неповёрнутого.

    Прокси, собранные до фикса автоповорота, лежат искажёнными и на боку. Имя файла
    считается от пути/mtime/size — без метки поворота такой прокси переиспользовался
    бы молча, и юзер видел бы кривой черновик при уже исправленном коде.
    """
    from core import draftrender
    src = _fake_src(tmp_path)
    monkeypatch.setattr(draftrender, "_display_dims", lambda s: (2160, 3840, True))
    rotated = draftrender.preview_path(src, 720, str(tmp_path))
    rotated_draft = draftrender._proxy_path(src, 720, 1280, str(tmp_path))
    monkeypatch.setattr(draftrender, "_display_dims", lambda s: (2160, 3840, False))
    plain = draftrender.preview_path(src, 720, str(tmp_path))
    plain_draft = draftrender._proxy_path(src, 720, 1280, str(tmp_path))
    assert rotated != plain, "повёрнутый исходник переиспользует до-фиксовый прокси"
    assert rotated_draft != plain_draft, "то же самое в прокси чернового"


def test_unrotated_proxy_key_is_unchanged(tmp_path, monkeypatch):
    """У обычных файлов ключ прежний — иначе все прокси пересоберутся на ровном месте.

    Метка `:g` в конце — намеренная СМЕНА формата: старые прокси несли ключевые кадры
    раз в 10 секунд (дефолтный GOP 250), и seek в браузере гонял декодер до 10с назад.
    Без метки кэш отдал бы старые прокси новому коду — именно они и тормозили."""
    from core import draftrender
    import hashlib
    import os as _os
    src = _fake_src(tmp_path)
    monkeypatch.setattr(draftrender, "_display_dims", lambda s: (1080, 1920, False))
    st = _os.stat(src)
    old = f"{_os.path.abspath(src)}|{int(st.st_mtime)}|{st.st_size}|prev720:g"
    want = "pv_" + hashlib.sha1(old.encode("utf-8")).hexdigest()[:12] + ".mp4"
    assert _os.path.basename(draftrender.preview_path(src, 720, str(tmp_path))) == want
    # метка формата обязана отличать до-фиксовые и после-фиксовые прокси
    assert ":g" in old and draftrender.preview_path(src, 720, str(tmp_path)) != \
        os.path.join(str(tmp_path), "pv_" + hashlib.sha1(
            old.replace(":g", "").encode("utf-8")).hexdigest()[:12] + ".mp4")


def test_display_dims_is_cached(tmp_path, monkeypatch):
    """ffprobe не гоняем на каждый вызов: план прокси считается на каждое открытие
    предпросмотра и на каждый опрос прогресса сборки."""
    from core import draftrender
    draftrender._DIMS_CACHE.clear()
    src = _fake_src(tmp_path, "cached.mp4")
    calls = []

    class _R:
        stdout = "3840\n2160\n90\n"

    monkeypatch.setattr(draftrender.subprocess, "run", lambda *a, **k: (calls.append(1), _R())[1])
    assert draftrender._display_dims(src) == (2160, 3840, True)
    assert draftrender._display_dims(src) == (2160, 3840, True)
    assert len(calls) == 1, "ffprobe вызван повторно — кэш не работает"

def test_omni_cache_requires_matching_intervals(tmp_path):
    """.omni.json переиспользуется ТОЛЬКО когда совпадает с роликом: та же длина И те
    же интервалы. Битый JSON, чужой файл с тем же числом интервалов и другой сдвиг
    не должны подставиться молча (аудит 2026-08-13: чужой транскрипт после VAD)."""
    import json
    from core import omni_cut
    omf = str(tmp_path / "cut.omni.json")
    intervals = [(1.0, 3.0), (4.0, 7.0)]
    good = [{"start": 1.0, "end": 3.0, "text": "один"},
            {"start": 4.0, "end": 7.0, "text": "два"}]
    json.dump(good, open(omf, "w", encoding="utf-8"))
    assert omni_cut._load_omni_cache(omf, intervals) == good

    # битый JSON — как после краха в момент записи: читаемо? нет, перегенерируем
    open(omf, "w", encoding="utf-8").write("{битый")
    assert omni_cut._load_omni_cache(omf, intervals) is None

    # чужой ролик с тем же ЧИСЛОМ интервалов, но другим таймингом — не берём
    json.dump([{"start": 100.0, "end": 102.0, "text": "чуж"},
               {"start": 103.0, "end": 106.0, "text": "ой"}],
              open(omf, "w", encoding="utf-8"))
    assert omni_cut._load_omni_cache(omf, intervals) is None

    # другой длины — тоже мимо
    json.dump([good[0]], open(omf, "w", encoding="utf-8"))
    assert omni_cut._load_omni_cache(omf, intervals) is None
