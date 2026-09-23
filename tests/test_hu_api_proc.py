# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: API и процессы.

1. Allowlist /api/media (только медиафайлы, отказ для .txt/.bak).
2. _block_dns_rebinding (Sec-Fetch-Site cross-site/same-site запрещены для любых методов).
3. MAX_CONTENT_LENGTH в webui.py и 413 JSON.
4. Зажатие pps в [10, 1000] в /api/waveform.
5. Нормализация jobs в api_render_run до старта потока.
6. Защита core/assets.py от не-словарей, не-строк, traversal и битого JSON.
7. Сторож простоя и отмена rclone в api/gdrive.py и /api/cancel.
8. Сторож простоя aerender в api/render.py _run_proc.
"""
import json
import sys
import types
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def client():
    from webui import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_job_state():
    """GDJOB и RJOB — общие на процесс: `failed`/`items`/`log` от здешних проверок
    иначе приезжают в соседние файлы (`test_gdrive` читает статус джоба). Сами флаги
    отмены убирает общая фикстура tests/conftest.py — их ставит `POST /api/cancel`."""
    yield
    from api import gdrive, render
    with gdrive.GDLOCK:
        gdrive.GDJOB.update(gdrive.GDFRESH, running=False, done=False, failed=None,
                            log=[], log_base=0, url="", started=0)
    with render.RLOCK:
        render.RJOB.update(running=False, done=False, failed=[], log=[],
                           items=[], result=[], pct=None, cur="")


def test_media_allowlist(client, tmp_path):
    """1. /api/media отдаёт только разрешённые расширения медиа, прочее -> 403."""
    txt = tmp_path / "secret.txt"
    txt.write_text("secret_content", encoding="utf-8")
    r = client.get(f"/api/media?path={txt}", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 403

    bak = tmp_path / "ai_config.json.bak"
    bak.write_text("{}", encoding="utf-8")
    r = client.get(f"/api/media?path={bak}", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 403

    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"fake-mp4-data")
    r = client.get(f"/api/media?path={mp4}", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    assert r.data == b"fake-mp4-data"


def test_dns_rebinding_cross_site_get(client, tmp_path):
    """2. GET-запросы с Sec-Fetch-Site: cross-site/same-site должны блокироваться (403)."""
    mp4 = tmp_path / "preview.mp4"
    mp4.write_bytes(b"fake-mp4")

    # cross-site -> 403
    r = client.get(f"/api/media?path={mp4}",
                   headers={"Host": "127.0.0.1:5001", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert r.get_json().get("err") == "forbidden_origin"

    # same-site -> 403
    r = client.get(f"/api/media?path={mp4}",
                   headers={"Host": "127.0.0.1:5001", "Sec-Fetch-Site": "same-site"})
    assert r.status_code == 403

    # same-origin -> 200
    r = client.get(f"/api/media?path={mp4}",
                   headers={"Host": "127.0.0.1:5001", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200


def test_max_content_length(client):
    """3. Превышение MAX_CONTENT_LENGTH отдаёт 413 в формате JSON."""
    from webui import app
    assert "MAX_CONTENT_LENGTH" in app.config
    limit = app.config["MAX_CONTENT_LENGTH"]
    assert limit > 0
    # Отправляем запрос, превышающий лимит
    big_data = "x" * (limit + 1024)
    r = client.post("/api/ui_state", data=big_data, content_type="application/json")
    assert r.status_code == 413
    assert r.is_json
    d = r.get_json()
    assert d.get("err") == "http_error"
    assert d.get("err_vars", {}).get("code") == 413


def test_waveform_pps_clamped(client, tmp_path, monkeypatch):
    """4. pps зажимается в [10, 1000] и кэш пишется с зажатым значением."""
    fake_audio = tmp_path / "clip.wav"
    fake_audio.write_bytes(b"RIFFfake")

    # Поддельный librosa и numpy для работы в CI
    import numpy as np
    fake_librosa = types.ModuleType("librosa")
    fake_librosa.load = lambda path, sr=16000, mono=True: (np.zeros(32000), 16000)
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)

    # Запрос с pps=-1 -> clamp to 10
    r1 = client.get(f"/api/waveform?path={fake_audio}&pps=-1",
                    headers={"Host": "127.0.0.1:5001"})
    assert r1.status_code == 200
    assert r1.get_json().get("pps") == 10
    assert (tmp_path / "clip.wav.peaks10.json").is_file()
    assert not (tmp_path / "clip.wav.peaks-1.json").exists()

    # Запрос с pps=100000 -> clamp to 1000
    r2 = client.get(f"/api/waveform?path={fake_audio}&pps=100000",
                    headers={"Host": "127.0.0.1:5001"})
    assert r2.status_code == 200
    assert r2.get_json().get("pps") == 1000
    assert (tmp_path / "clip.wav.peaks1000.json").is_file()
    assert not (tmp_path / "clip.wav.peaks100000.json").exists()


def test_render_run_normalizes_before_response(client, monkeypatch):
    """5. api_render_run валидирует jobs до ответа и не стартует поток при кривом наборе."""
    from api import render
    started_threads = []

    def fake_thread_start(self):
        started_threads.append(self)

    monkeypatch.setattr("threading.Thread.start", fake_thread_start)

    r = client.post("/api/render_run",
                    json={"jobs": [{"xml": "nonexistent_xml_file.xml"}]},
                    headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    data = r.get_json()
    assert not data.get("ok")
    assert "error" in data or "err" in data
    assert len(started_threads) == 0
    assert not render.RJOB["running"]


def test_assets_resilience(tmp_path, monkeypatch):
    """6. assets.py: не-словарь, не-строки, traversal и битый JSON — с предупреждением в лог."""
    from core import assets

    warned = []

    class _Log:
        def warning(self, msg, *a):
            warned.append(msg % a if a else str(msg))

    monkeypatch.setattr(assets, "log", _Log())

    # 6a. assets.json не словарь (например, список)
    d = tmp_path / "assets"
    d.mkdir()
    cfg = d / "assets.json"
    cfg.write_text("[1, 2, 3]", encoding="utf-8")
    resolve_fn = assets.resolver(str(tmp_path))
    assert resolve_fn("whoosh") == ""
    assert any("не словарь" in m for m in warned), warned

    # 6b. Значение не строка
    cfg.write_text(json.dumps({"whoosh": 42}), encoding="utf-8")
    resolve_fn = assets.resolver(str(tmp_path))
    assert resolve_fn("whoosh") == ""

    # 6c. Traversal: выход наружу папки assets
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"secret")
    cfg.write_text(json.dumps({"whoosh": "../secret.wav"}), encoding="utf-8")
    resolve_fn = assets.resolver(str(tmp_path))
    assert resolve_fn("whoosh") == ""

    # 6d. Нечитаемый JSON — роль пропускается, причина в логе (а не молчаливое «SFX пропали»)
    warned.clear()
    cfg.write_text("{bad json...", encoding="utf-8")
    resolve_fn = assets.resolver(str(tmp_path))
    assert resolve_fn("whoosh") == ""
    assert any("Не удалось прочитать" in m for m in warned), warned

    # 6e. Живой файл внутри assets по-прежнему находится
    (d / "pop.wav").write_bytes(b"RIFF")
    cfg.write_text(json.dumps({"whoosh": "pop.wav"}), encoding="utf-8")
    assert assets.resolver(str(tmp_path))("whoosh") == str(d / "pop.wav")


def test_gdrive_watchdog_and_cancel(client, monkeypatch):
    """7. Сторож простоя rclone и отмена через /api/cancel."""
    from api import gdrive

    # 7a. Проверка отмены через /api/cancel
    proc_mock = MagicMock()
    proc_mock.poll.return_value = None
    proc_mock.pid = 99999
    monkeypatch.setattr(gdrive, "GDPROC", proc_mock)
    kill_called = []
    monkeypatch.setattr(gdrive, "_kill_proc", lambda p: kill_called.append(p))

    with gdrive.GDLOCK:
        gdrive.GDJOB["running"] = True
        gdrive.GDJOB["cancel"] = False

    r = client.post("/api/cancel", headers={"Host": "127.0.0.1:5001"})
    assert r.status_code == 200
    assert gdrive.GDJOB["cancel"] is True
    assert len(kill_called) == 1
    assert kill_called[0] is proc_mock

    # 7b. Сторож простоя rclone
    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", 0.05)

    class SilentProc:
        def __init__(self):
            self.pid = 99998
            self.stdout = []  # Никаких строк вывода
            self._killed = False
            self.returncode = None

        def poll(self):
            return 0 if self._killed else None

        def wait(self):
            return -137 if self._killed else 0

    silent_p = SilentProc()
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: silent_p)
    monkeypatch.setattr(gdrive, "_kill_proc", lambda p: setattr(p, "_killed", True))

    log_emits = []
    rc = gdrive._run_rclone(["fake", "rclone"], lambda line: log_emits.append(line))
    assert rc == gdrive._RCLONE_STALLED
    assert silent_p._killed is True

    # 7c. Итог виден в GDJOB понятной строкой, а не кодом возврата: сторож — «нет данных
    # N минут», «Стоп» — «скачивание остановлено» (без rclone и сети: подменяем _run_rclone).
    url = "https://drive.google.com/file/d/abcdefghijkl/view"
    monkeypatch.setattr(gdrive, "RCLONE_STALL_SEC", 10 * 60)     # вернуть боевые 10 минут
    monkeypatch.setattr(gdrive, "_run_rclone", lambda *a, **k: gdrive._RCLONE_STALLED)
    with gdrive.GDLOCK:
        gdrive.GDJOB.update(gdrive.GDFRESH, running=True, done=False, failed=None, log=[])
    gdrive._download_job(["fake", "rclone"], url)
    assert gdrive.GDJOB["cur"] == "нет данных 10 минут", gdrive.GDJOB["cur"]
    assert gdrive.GDJOB["failed"] is True and gdrive.GDJOB["done"] is True

    monkeypatch.setattr(gdrive, "_run_rclone", lambda *a, **k: 1)
    with gdrive.GDLOCK:
        gdrive.GDJOB.update(gdrive.GDFRESH, running=True, done=False, failed=None, log=[])
        gdrive.GDJOB["cancel"] = True          # как его ставит gdrive_kill из /api/cancel
    gdrive._download_job(["fake", "rclone"], url)
    assert gdrive.GDJOB["cur"] == "скачивание остановлено", gdrive.GDJOB["cur"]


def test_aerender_watchdog(monkeypatch):
    """8. Сторож простоя aerender в _run_proc: молчащий процесс снимается, причина —
    в логе рендера и в failed, флаг отмены соседнего джоба тут ни при чём."""
    from api import render

    monkeypatch.setattr(render, "AE_STALL_KILL_SEC", 0.05)
    monkeypatch.setattr(render, "AE_STALL_WARN_SEC", 0.02)

    class SilentProc:
        def __init__(self):
            self.pid = 88888
            self.stdout = []  # Молчит
            self._killed = False
            self.returncode = None

        def poll(self):
            return 0 if self._killed else None

        def wait(self):
            return -137 if self._killed else 0

    silent_p = SilentProc()
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: silent_p)
    monkeypatch.setattr(render, "_kill_proc", lambda p: setattr(p, "_killed", True))

    # RJOB — общий на весь модуль: без сброса сюда приезжает cancel=True от /api/cancel
    # предыдущего теста, и _run_proc снимает процесс СРАЗУ как отменённый, а не сторожем.
    with render.RLOCK:
        render.RJOB.update(cancel=False, failed=[], log=[],
                           items=[{"name": "clip1", "stage": "render", "pct": 0.0}])

    rc = render._run_proc(["fake", "aerender"], item_name="clip1")
    assert rc == render.AE_STALLED
    assert silent_p._killed is True
    # Проверяем, что в RJOB зафиксирована понятная ошибка
    failed_items = [it for it in render.RJOB.get("items", []) if it.get("stage") == "error"]
    assert len(failed_items) == 1
    assert "не отвечает" in failed_items[0].get("reason", "")
    assert any("не отвечает" in f.get("reason", "") for f in render.RJOB.get("failed", []))
    assert any("сторож простоя" in str(ln) for ln in render.RJOB.get("log", []))
