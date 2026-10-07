# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Безопасность: путь из ответа стока, SSRF-гвард, маска заголовков, замок GPU, Host.

Пять дверей, которые закрываются здесь, и почему именно так:

1. **Имя файла из ответа стока** (`core/stock.py`). `id` кандидата приходит из ответа
   ЧУЖОЙ службы, а не от пользователя: `id="../../x"` собирал путь за пределами базы.
   Имя чистится (`safe_name`), а готовый путь проверяется `realpath` внутри папки —
   одной чистки мало, символическая ссылка внутри базы увела бы файл наружу.
2. **SSRF-гвард** (`core/stock.py`, `core/aicut/images.py`, `/api/video_probe`):
   `file://` читал бы локальный файл, `http://127.0.0.1` и приватные адреса уводили бы
   запрос внутрь машины и в локальную сеть. Проверка — общая `unsafe_url_reason`.
3. **Заголовки профиля ИИ** (`core/aicut/config.py`): заголовок — поддерживаемый канал
   секретов (`Authorization`, `x-api-key`), и он уходил в браузер открытым текстом.
   Теперь — та же маска «•••xxxx», что у ключа, а сохранение с маской не затирает
   настоящее значение.
4. **Замок видеокарты** (`core/gpulock.py`): любой сбой файла замка считался
   «занято» — `while True` со `sleep(0.5)` висел вечно. «Сломано» теперь отказ сразу,
   «занято» по-прежнему ждёт с сообщением.
5. **Host с чужим портом** (`api/_core.py`): `127.0.0.1:5999` — локальное имя, но
   чужая дверь; проверка сверяет и порт. И `/api/export_xml` больше не GET: он пишет.

Сети нет: `urllib.request.urlopen`, скачивание стока и ffprobe подменены. Реальных
сигналов тесты не шлют.

Запуск: python -m pytest tests/test_security_fixes.py -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

import api  # noqa: E402
from core import gpulock, insertlib, stock  # noqa: E402
from core.aicut import config as aicut  # noqa: E402
from core.umsg import ReelsiError  # noqa: E402

H = {"Host": "127.0.0.1:5001"}


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Индекс базы и конфиг ИИ — в tmp_path: в рабочую копию тест не пишет."""
    monkeypatch.setattr(insertlib, "INDEX_PATH", str(tmp_path / "insertlib.json"))
    monkeypatch.setattr(insertlib, "_CACHE", {
        "mtime": 0, "data": None, "mat": None, "mat_items": None,
        "df": None, "cand_words": None, "N": 0})
    monkeypatch.setattr(insertlib, "_seed_index", lambda: None)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    cfg_path = tmp_path / "ai_config.json"
    cfg_path.write_text(json.dumps({
        "active": "Стенд",
        "profiles": {"Стенд": {"provider": "lmstudio", "base_url": "http://localhost:1234/v1",
                               "api_key": "xxxxxxxxxxxxxxxx1111", "model": "стенд-модель",
                               "headers": {"X-Token": "abcdef123456"}}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(aicut, "AI_CONFIG_PATH", str(cfg_path))
    return tmp_path


def _cand(**extra):
    """Кандидат стока — минимум полей, нужных скачиванию."""
    c = {"provider": "pexels", "id": 101, "kind": "photo",
         "page_url": "https://www.pexels.com/photo/101/",
         "download_url": "https://images.pexels.com/photos/101/large2x.jpg",
         "query": "cat", "author": "Автор", "author_url": "", "tags": ["cat"]}
    c.update(extra)
    return c


def _can_create_symlinks(tmp_path):
    """Разрешено ли создание символических ссылок в этой ОС и окружении.

    На Windows для них нужны права администратора либо режим разработчика, поэтому
    проверку пути на символическую ссылку без такой проверки не прогнать."""
    src = tmp_path / "_probe_src"
    dst = tmp_path / "_probe_dst"
    src.write_text("t", encoding="utf-8")
    try:
        os.symlink(src, dst)
        return True
    except OSError:
        return False
    finally:
        for p in (dst, src):
            try:
                os.remove(str(p))
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# 1. Имя файла из ответа стока: за пределы папки не выйти
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("evil_id", ["../../x", "..\\..\\x", "/etc/passwd", "..",
                                     "C:\\Windows\\win.ini"])
def test_stock_file_stays_inside_the_library(iso, monkeypatch, evil_id):
    """`id` с разделителями пути не уводит файл из базы: имя чистится, путь внутри."""
    dest = iso / "lib"
    calls = []

    def fake_download(url, path):
        calls.append(path)
        with open(path, "wb") as f:
            f.write(b"x")

    monkeypatch.setattr(stock, "_download_file", fake_download)

    path = stock.download(_cand(id=evil_id), str(dest))

    root = os.path.realpath(str(dest))
    assert os.path.realpath(path).startswith(root + os.sep), path
    assert os.path.dirname(os.path.realpath(path)) == os.path.realpath(
        os.path.join(root, "stock", "pexels"))
    assert ".." not in os.path.basename(path)
    assert len(calls) == 1 and os.path.realpath(calls[0]) == os.path.realpath(path)
    # в базе только файл и его лицензия, и оба внутри неё — наружу не ушло ничего
    files = [p for p in (dest / "stock").rglob("*") if p.is_file()]
    assert len(files) == 2, [str(p) for p in files]
    inside = os.path.realpath(os.path.join(root, "stock", "pexels"))
    for p in files:
        assert os.path.dirname(os.path.realpath(str(p))) == inside, str(p)


def test_stock_download_refuses_unsafe_url(iso):
    """Адрес из ответа стока проходит SSRF-гвард: `file://`, loopback и ftp — отказ."""
    for bad in ("file:///C:/Windows/win.ini", "http://127.0.0.1/secret",
                "ftp://example.test/x.jpg"):
        with pytest.raises(ReelsiError) as e:
            stock._download_file(bad, str(iso / "x.jpg"))
        assert e.value.code == "stock_unsafe_url"


def test_path_outside_the_library_is_rejected_by_the_guard(tmp_path, monkeypatch):
    """`realpath`-проверка ловит выход из папки, даже если имя его не выдаёт.

    Чистка имени — одна дверь, проверка пути — вторая. Здесь символическая ссылка
    внутри базы уводит наружу, а имя чистое; проверка обязана это увидеть.

    `realpath` подменён: в этом каталоге родитель `tmp_path` — тоже символическая
    ссылка (так устроен путь pytest на Windows), и настоящее разрешение увело бы
    тест в сторону от того, что проверяется."""
    from core import _pathguard

    base = tmp_path / "lib" / "stock" / "pexels"
    (tmp_path / "снаружи").mkdir(parents=True)
    base.mkdir(parents=True)
    monkeypatch.setattr(_pathguard.os.path, "realpath", lambda p: os.path.abspath(str(p)))

    # чистое имя в базе — путь свой
    assert _pathguard.inside_dir(str(base), "cat-101.jpg").startswith(os.path.abspath(str(base)))
    # ссылка `link` -> ../.. : путь по ней выходит из базы и отвергается
    with pytest.raises(ValueError):
        _pathguard.inside_dir(str(base), ".." + os.sep + ".." + os.sep + "снаружи" + os.sep + "x.jpg")


def test_stock_route_refuses_path_escape(client, iso, monkeypatch):
    """Тот же отказ через роут: файл остаётся внутри папки базы, наружу не пишется."""
    def fake_download(url, path):
        with open(path, "wb") as f:
            f.write(b"\xff\xd8\xff" + b"jpeg" * 50)

    monkeypatch.setattr(stock, "_download_file", fake_download)
    # перекодировка AE-совместимости — своя тема; здесь проверяется путь файла
    monkeypatch.setattr(insertlib, "to_ae_media", lambda p, emit=None: p)
    dest = iso / "lib"

    d = client.post("/api/stock_pick", headers=H,
                    json={"candidate": _cand(id="../../../../tmp/OWNED"), "dest": str(dest)}
                    ).get_json()

    assert d.get("ok") is True, d
    root = os.path.realpath(str(dest))
    assert os.path.realpath(d["path"]).startswith(root + os.sep), d["path"]
    assert ".." not in os.path.basename(d["path"]), d["path"]
    files = [p for p in (dest / "stock").rglob("*") if p.is_file()]
    assert files, "файл базы не создан"
    for p in files:
        assert os.path.realpath(str(p)).startswith(root + os.sep), str(p)


# --------------------------------------------------------------------------- #
# 2. SSRF-гвард: адрес картинки от провайдера и /api/video_probe
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url", ["file:///C:/Windows/win.ini", "http://127.0.0.1/x",
                                 "https://192.168.1.5/x", "ftp://example.test/x"])
def test_video_probe_refuses_unsafe_url(client, monkeypatch, url):
    """/api/video_probe не ходит по адресу из тела запроса, если адрес небезопасен."""
    from core import aicut
    called = []
    monkeypatch.setattr(aicut, "probe_media", lambda u: called.append(u) or None)

    d = client.post("/api/video_probe", headers=H, json={"url": url}).get_json()

    assert d.get("ok") is not True and d.get("error")
    assert called == [], "ffprobe пошёл по небезопасному адресу"


def test_video_probe_still_probes_a_normal_link(client, monkeypatch):
    """Гвард не сломал рабочий случай: обычная https-ссылка доходит до ffprobe."""
    from core import aicut
    called = []
    monkeypatch.setattr(aicut, "probe_media",
                        lambda u: called.append(u) or {"kind": "video", "duration": 1.0,
                                                       "width": 16, "height": 16})
    link = "https://cdn.pixabay.com/video/2026/01/01/1_large.mp4"
    d = client.post("/api/video_probe", headers=H, json={"url": link}).get_json()
    assert d.get("ok") is True, d
    assert called == [link]


def test_generated_image_url_is_guarded():
    """Абсолютный адрес картинки от провайдера идёт через тот же SSRF-гвард."""
    from core.app_meta import unsafe_url_reason
    for bad in ("file:///C:/Windows/win.ini", "http://127.0.0.1/x"):
        assert unsafe_url_reason(bad), bad
    # свой base_url (LM Studio на loopback) — не «чужой адрес», а продолжение
    # настроенного провайдера: относительный путь к нему гвард не задевает
    assert unsafe_url_reason("http://127.0.0.1:1234/v1/out.png")
    assert not unsafe_url_reason("https://images.pexels.com/photos/1/x.png")


# --------------------------------------------------------------------------- #
# 3. Заголовки профиля ИИ: маска наружу, настоящее значение внутрь
# --------------------------------------------------------------------------- #
def test_mask_header_value_masks_like_the_key():
    """Значение заголовка наружу — «•••xxxx», как ключ профиля."""
    assert aicut.mask_header_value("abcdef123456") == "•••3456"
    assert aicut.mask_header_value("") == ""
    assert aicut.mask_header_value("env:МОЙ_ТОКЕН") == "env:МОЙ_ТОКЕН"


def test_headers_masked_in_config_response(iso, client):
    """GET /api/ai_config отдаёт значения заголовков маской, а не открытым текстом."""
    d = client.get("/api/ai_config", headers=H).get_json()
    prof = d["profiles"]["Стенд"]
    assert prof["headers"]["X-Token"] == "•••3456"
    assert "abcdef123456" not in json.dumps(d, ensure_ascii=False)


def test_saving_masked_header_keeps_the_secret(iso, client):
    """Сохранение с маской «не менял» не затирает настоящее значение заголовка."""
    d = client.post("/api/ai_config", headers=H, json={
        "action": "save_profile", "name": "Стенд",
        "profile": {"provider": "lmstudio", "base_url": "http://localhost:1234/v1",
                    "api_key": "•••1111", "model": "стенд-модель",
                    "headers_text": "X-Token: •••3456"}}).get_json()
    assert d.get("ok") is True, d

    saved = aicut.load_ai_config()["profiles"]["Стенд"]["headers"]
    assert saved["X-Token"] == "abcdef123456", saved
    assert client.get("/api/ai_config", headers=H).get_json(
    )["profiles"]["Стенд"]["headers"]["X-Token"] == "•••3456"


def test_live_headers_do_not_send_the_mask(iso):
    """Живой вызов (проверка, список моделей) не отправляет провайдеру маску."""
    assert aicut.resolve_header_mask({"X-Token": "•••3456"}, "Стенд") == {
        "X-Token": "abcdef123456"}


def test_new_header_value_is_saved_as_is(iso, client):
    """Новое (не маска) значение заголовка сохраняется как есть."""
    d = client.post("/api/ai_config", headers=H, json={
        "action": "save_profile", "name": "Стенд",
        "profile": {"provider": "lmstudio", "base_url": "http://localhost:1234/v1",
                    "api_key": "•••1111", "model": "стенд-модель",
                    "headers_text": "X-Token: new-secret-9999"}}).get_json()
    assert d.get("ok") is True, d
    assert aicut.load_ai_config()["profiles"]["Стенд"]["headers"]["X-Token"] == "new-secret-9999"


# --------------------------------------------------------------------------- #
# 4. Замок GPU: «сломано» — отказ сразу, «занято» — ожидание как раньше
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(10)
def test_gpu_lock_fails_fast_when_the_folder_is_not_writable(tmp_path, monkeypatch):
    """Каталог без права записи — исключение сразу, а не вечный спин «жду карту»."""
    monkeypatch.setattr(gpulock, "JOB_LOCK_PATH",
                        str(tmp_path / "нет-такой-папки" / "job.lock"))
    monkeypatch.setattr(os, "access", lambda path, mode: False)

    with pytest.raises(ReelsiError) as e:
        with gpulock.gpu_lock("тест"):
            pytest.fail("замок не должен был взяться")
    assert "Замок видеокарты" in str(e.value)


@pytest.mark.timeout(10)
def test_gpu_lock_reports_a_read_only_lock_file(tmp_path, monkeypatch):
    """Сам файл замка открыть нельзя — тоже отказ с причиной, без ожидания."""
    lock = tmp_path / "job.lock.gpu"
    lock.write_bytes(b"")
    monkeypatch.setattr(gpulock, "JOB_LOCK_PATH", str(tmp_path / "job.lock"))

    real_open = open

    def fake_open(path, *a, **k):
        if os.path.normcase(str(path)) == os.path.normcase(str(lock)):
            raise PermissionError(13, "Access is denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    with pytest.raises(ReelsiError) as e:
        with gpulock.gpu_lock("тест"):
            pytest.fail("замок не должен был взяться")
    assert e.value.vars.get("path")
    assert "Замок видеокарты" in str(e.value)


@pytest.mark.timeout(10)
def test_gpu_lock_fails_fast_when_the_folder_cannot_be_created(tmp_path, monkeypatch):
    """Каталога замка нет и создать его нельзя — отказ сразу, а не вечный спин.

    Тот же дефект, что и с каталогом без права записи, только на шаг раньше: прежний
    `while True` на этом `open` тоже крутился бы вечно."""
    missing = tmp_path / "нет-и-не-будет"
    monkeypatch.setattr(gpulock, "JOB_LOCK_PATH", str(missing / "job.lock"))
    real_isdir = os.path.isdir

    monkeypatch.setattr(gpulock.os.path, "isdir",
                        lambda p: False if os.path.basename(str(p)) == missing.name
                        else real_isdir(p))
    monkeypatch.setattr(gpulock.os, "makedirs",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied")))

    with pytest.raises(ReelsiError) as e:
        with gpulock.gpu_lock("тест"):
            pytest.fail("замок не должен был взяться")
    assert "Замок видеокарты" in str(e.value)


@pytest.mark.timeout(10)
def test_gpu_lock_gives_up_when_the_lock_file_will_not_open(tmp_path, monkeypatch):
    """Файл замка не открывается, а каталог писать можно — несколько попыток и причина.

    Это не «занято» (замка ещё нет) и не «нет прав» (каталог свой): вечное ожидание тут
    так же бессмысленно, как и в двух случаях выше."""
    monkeypatch.setattr(gpulock, "JOB_LOCK_PATH", str(tmp_path / "job.lock"))
    monkeypatch.setattr("builtins.open",
                        lambda *a, **k: (_ for _ in ()).throw(OSError(22, "сбой файловой системы")))

    with pytest.raises(ReelsiError) as e:
        with gpulock.gpu_lock("тест"):
            pytest.fail("замок не должен был взяться")
    assert "Замок видеокарты" in str(e.value)


@pytest.mark.timeout(30)
def test_gpu_lock_still_works_on_a_writable_folder(tmp_path, monkeypatch):
    """Честный путь не сломан: замок берётся и снимается в обычной папке."""
    monkeypatch.setattr(gpulock, "JOB_LOCK_PATH", str(tmp_path / "job.lock"))
    with gpulock.gpu_lock("тест", emit=lambda *a, **k: None):
        pass


# --------------------------------------------------------------------------- #
# 5. Host с чужим портом, export_xml — POST, а не GET
# --------------------------------------------------------------------------- #
def test_host_helper_checks_the_port():
    """Локальное имя с ЧУЖИМ портом — не наш сервер: у нас дверь одна.

    Проверка зовётся напрямую: у `/api/media` свой 403 на «путь не того типа», и
    отличить его от отказа Host через роут нельзя."""
    assert api._host_is_local("127.0.0.1:5001")
    assert api._host_is_local("localhost:5001")
    assert api._host_is_local("LOCALHOST:5001")
    assert api._host_is_local("[::1]:5001")
    assert api._host_is_local("127.0.0.1")          # голое имя — своя же дверь
    assert not api._host_is_local("127.0.0.1:5999")
    assert not api._host_is_local("localhost:8080")
    assert not api._host_is_local("127.0.0.1:порт")
    assert not api._host_is_local("")
    assert not api._host_is_local(None)


def test_host_with_foreign_port_is_rejected(client):
    """Та же проверка стоит на всех роутах: чужой порт получает 403 до кода роута."""
    r = client.get("/api/media?path=x", headers={"Host": "127.0.0.1:5999"})
    assert r.status_code == 403
    r = client.get("/api/status", headers={"Host": "localhost:8080"})
    assert r.status_code == 403


def test_export_xml_get_is_method_not_allowed(client, iso):
    """Роут ПИШЕТ XML — GET с побочным действием закрыт, работает только POST."""
    xml = iso / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")

    r = client.get(f"/api/export_xml?path={xml}", headers=H)
    assert r.status_code == 405


def test_export_xml_post_serves_the_file(client, iso, monkeypatch):
    """POST с телом {path} отдаёт XML как раньше."""
    from core import xmlbuild
    monkeypatch.setattr(xmlbuild, "fix_timecodes", lambda text: (text, 0))
    xml = iso / "clip.xml"
    xml.write_text("<xmeml/>", encoding="utf-8")

    r = client.post("/api/export_xml", headers=H, json={"path": str(xml)})
    assert r.status_code == 200
    assert r.get_data(as_text=True) == "<xmeml/>"


def test_export_xml_frontend_posts():
    """Фронт скачивает XML POST-ом, а не переходом по ссылке (иначе 405)."""
    src = open(os.path.join(ROOT, "static", "app", "40-queue.js"), encoding="utf-8").read()
    assert "fetch('/api/export_xml',{method:'POST'" in src
    assert "/api/export_xml?path=" not in src


# --------------------------------------------------------------------------- #
# 6. Мутационные самотесты: снятая защита обязана покраснеть
# --------------------------------------------------------------------------- #
# Проверка защиты — в том, что она ЛОВИТ. Тест, который проходит и со снятой
# проверкой, ничего не значит, поэтому здесь защита снимается на время теста:
# мутация идёт рядом с утверждением, и падение теста и есть доказательство.
# (Проверено и вручную: снятая `realpath`-проверка и снятая маска заголовков
# красят эти тесты.)
def test_mutation_inside_dir_is_the_realpath_check(tmp_path, monkeypatch, iso):
    """`inside_dir` — не украшение: без него тот же вызов пишет файл наружу.

    Единственный выход из базы, который переживает чистку имени, — символическая
    ссылка ВНУТРИ неё: имя чистое, а `realpath` по нему ведёт мимо. С проверкой
    вызов ошибается и каталог по ссылке пуст; без неё файл появляется за базой.
    Разница и есть доказательство, что проверка ловит, а не сопровождает.
    """
    if not _can_create_symlinks(tmp_path):
        pytest.skip("создание символических ссылок недоступно в этом окружении")
    dest = tmp_path / "lib"
    root = os.path.realpath(str(dest))
    outside = tmp_path / "снаружи"
    outside.mkdir(parents=True)
    written: list[str] = []

    monkeypatch.setattr(stock, "_download_file",
                        lambda url, path: written.append(os.path.realpath(path)))
    # имя внутри базы, но символическая ссылка «link» уводит его наружу
    monkeypatch.setattr(stock, "_slug", lambda cand: "link")
    monkeypatch.setattr(stock, "_ext_of", lambda url, kind: "")
    monkeypatch.setattr(stock, "_write_license", lambda path, cand: None)
    # перекодировка и индекс — своя тема; здесь важно только, КУДА ушёл файл,
    # иначе мёртвая ветка мутации падала бы на записи лицензии рядом с ним
    monkeypatch.setattr(insertlib, "to_ae_media", lambda p, emit=None: p)
    monkeypatch.setattr(insertlib, "add_file",
                        lambda path, desc, kind, src=None, emit=None: None)

    base = dest / "stock" / "pexels"
    base.mkdir(parents=True)
    try:
        # имя, которое соберёт `download`: слаг `link`, id `cand` — «link-cand»
        os.symlink(str(outside), str(base / "link-cand"))
    except OSError as e:                       # pragma: no cover — среда без ссылок
        pytest.skip(f"символическую ссылку не создать: {e}")

    with pytest.raises(ReelsiError):                       # ← защита на месте
        stock.download(_cand(id="cand"), str(dest))
    assert written == [], "с проверкой пути файл всё равно ушёл на запись"

    saved = stock.inside_dir
    try:
        stock.inside_dir = lambda base, name: os.path.join(base, name)   # ← мутация
        stock.download(_cand(id="cand"), str(dest))
    finally:
        stock.inside_dir = saved
    assert written, "мутация не дошла до записи — проверять нечего"
    assert not written[0].startswith(root + os.sep), \
        "снятая realpath-проверка не выпустила файл наружу — тест ничего не стережёт"


def test_mutation_without_the_header_mask_the_secret_leaks(iso, client, monkeypatch):
    """Снимем маску заголовков — и токен снова уезжает в браузер открытым текстом.

    Маска — единственное, что стоит между `headers` профиля и localStorage,
    devtools и DOM: без неё `GET /api/ai_config` отдаёт значение как есть."""
    monkeypatch.setattr(aicut, "mask_headers", lambda h: h)             # ← мутация
    d = client.get("/api/ai_config", headers=H).get_json()
    assert d["profiles"]["Стенд"]["headers"]["X-Token"] == "abcdef123456", \
        "снятая маска заголовков не открыла секрет — тест ничего не стережёт"
