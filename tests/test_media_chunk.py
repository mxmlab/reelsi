# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты: /api/media не занимает соединения браузера.

Браузер держит не больше 6 соединений на хост. <video>/<audio> просят
`Range: bytes=0-`, дочитывают буфер и замолкают, НЕ закрывая соединение: открытое
превью (камеры, их дублёры, музыка, SFX, видеовставки) съедало все шесть, и обычный
fetch ждал 5.3 с вместо 0.3 с — «сервер встаёт». Поэтому роут режет открытый (или
заведомо длинный) Range до MEDIA_CHUNK и только ветку отдачи на месте.

1. `bytes=0-` -> 206, ровно MEDIA_CHUNK байт из начала файла.
2. `bytes={size-1000}-` -> 206, 1000 байт (хвост короче куска).
3. `bytes=100-199` -> 206, ровно 100 байт (короткий диапазон не трогаем).
4. `bytes=0-{size-1}` (весь файл явно) -> сужено до MEDIA_CHUNK.
5. Без Range -> 200 целиком; `dl=1` -> вложение целиком.
6. Файл меньше куска, `bytes=0-` -> весь файл.
7. Запрещённое расширение и `_never_serve` -> прежние 403 (сужение их не обходит).
8. Суффиксный Range, список диапазонов, битый заголовок -> не трогаем.

Число куска тест берёт СВОЁ (CHUNK = 4 МиБ), а не из реализации: пока
ожидания читали MEDIA_CHUNK из api.files, мутация `MEDIA_CHUNK = 1024` проходила все
восемь проверок — тест мерил реализацию ею же. Импорт остаётся только на сверку: сколько
отдаёт роут, столько и заказано контрактом.
"""
import pytest

from api.files import MEDIA_CHUNK, _narrow_media_range

H = {"Host": "127.0.0.1:5001"}
CHUNK = 4 * 1024 * 1024          # 4 МиБ — кусок отдачи /api/media, число контракта
SIZE = 10 * 1024 * 1024          # ~10 МБ: заведомо больше куска в 4 МБ


@pytest.fixture
def client():
    from webui import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def big_mp4(tmp_path):
    """Файл ~10 МБ с узнаваемым содержимым (байт i == i % 251) — чтобы сверять
    не только длину тела, но и то, что отдан именно запрошенный кусок."""
    data = (bytes(range(251)) * (SIZE // 251 + 1))[:SIZE]
    p = tmp_path / "clip.mp4"
    p.write_bytes(data)
    return p, data


def test_open_range_is_capped(client, big_mp4):
    """1. `bytes=0-` -> 206, тело ровно 4 МиБ, Content-Range/Content-Length
    считает werkzeug по суженному диапазону.

    Размер сверяется с числом контракта (CHUNK), а не с MEDIA_CHUNK из api.files; сам
    MEDIA_CHUNK — отдельная проверка: он и есть то число, за которое тест держится.
    """
    assert MEDIA_CHUNK == CHUNK, "кусок отдачи /api/media уехал с 4 МиБ"
    assert _narrow_media_range("bytes=0-", SIZE) == f"bytes=0-{CHUNK - 1}", (
        "разбор Range режет открытый диапазон не по 4 МиБ")
    p, data = big_mp4
    r = client.get(f"/api/media?path={p}", headers={**H, "Range": "bytes=0-"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == f"bytes 0-{CHUNK - 1}/{SIZE}"
    assert r.headers["Content-Length"] == str(CHUNK)
    assert len(r.data) == CHUNK
    assert r.data == data[:CHUNK]


def test_open_range_from_tail_is_clamped_to_file_end(client, big_mp4):
    """2. `bytes={size-1000}-` -> 206 и ровно 1000 байт: хвост короче куска,
    min() не даёт уехать за конец файла."""
    p, data = big_mp4
    start = SIZE - 1000
    r = client.get(f"/api/media?path={p}", headers={**H, "Range": f"bytes={start}-"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == f"bytes {start}-{SIZE - 1}/{SIZE}"
    assert len(r.data) == 1000
    assert r.data == data[start:]


def test_short_range_untouched(client, big_mp4):
    """3. `bytes=100-199` -> 206 и ровно 100 байт: перемотку внутри файла не режем."""
    p, data = big_mp4
    r = client.get(f"/api/media?path={p}", headers={**H, "Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == f"bytes 100-199/{SIZE}"
    assert r.data == data[100:200]


def test_explicit_whole_file_is_capped(client, big_mp4):
    """4. `bytes=0-{size-1}` (весь файл явно) -> сужено до 4 МиБ: именно так
    просит видео, которому нужен весь файл."""
    p, _ = big_mp4
    r = client.get(f"/api/media?path={p}", headers={**H, "Range": f"bytes=0-{SIZE - 1}"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == f"bytes 0-{CHUNK - 1}/{SIZE}"
    assert len(r.data) == CHUNK


def test_no_range_and_download_are_whole(client, big_mp4):
    """5. Без Range — прежний ответ 200 целиком (загрузки и прочие потребители);
    `dl=1` — вложение целиком, сужение его не касается."""
    p, data = big_mp4
    r = client.get(f"/api/media?path={p}", headers=H)
    assert r.status_code == 200
    assert r.data == data

    r = client.get(f"/api/media?path={p}&dl=1", headers=H)
    assert r.status_code == 200
    assert r.data == data
    assert "attachment" in r.headers["Content-Disposition"]
    assert "clip.mp4" in r.headers["Content-Disposition"]


def test_small_file_open_range_untouched(client, tmp_path):
    """6. Файл меньше куска: `bytes=0-` отдаёт его весь (206 или 200 — как решает
    werkzeug), обрезать нечего."""
    data = bytes(range(256)) * 4              # 1 КБ < CHUNK (4 МиБ)
    p = tmp_path / "small.mp4"
    p.write_bytes(data)
    r = client.get(f"/api/media?path={p}", headers={**H, "Range": "bytes=0-"})
    assert r.status_code in (200, 206)
    assert r.data == data
    if r.status_code == 206:
        assert r.headers["Content-Range"] == f"bytes 0-{len(data) - 1}/{len(data)}"


def test_forbidden_paths_still_403(client, tmp_path):
    """7. Запрещённое расширение и секрет из `_never_serve` -> прежние 403, в том
    числе с Range: сужение идёт ПОСЛЕ проверок безопасности и обойти их не может."""
    txt = tmp_path / "notes.txt"
    txt.write_text("секрет", encoding="utf-8")
    cfg = tmp_path / "ai_config.json"
    cfg.write_text('{"profiles": {"x": {"api_key": "sk-СЕКРЕТ"}}}', encoding="utf-8")

    for p in (txt, cfg):
        for extra in ({}, {"Range": "bytes=0-"}):
            r = client.get(f"/api/media?path={p}", headers={**H, **extra})
            assert r.status_code == 403
            assert b"sk-" not in r.data


def test_other_range_forms_untouched(client, big_mp4):
    """8. Суффиксный диапазон, список диапазонов и битый заголовок — как было:
    разбор их не трогает, суффиксный отдаётся werkzeug'ом целиком."""
    p, data = big_mp4
    r = client.get(f"/api/media?path={p}", headers={**H, "Range": "bytes=-1000"})
    assert r.status_code == 206
    assert r.data == data[-1000:]

    for header in ("bytes=-1000", "bytes=0-99, 200-299", "bytes=abc", "items=0-", ""):
        assert _narrow_media_range(header, SIZE) is None
    assert _narrow_media_range(None, SIZE) is None
    assert _narrow_media_range("bytes=0-", 0) is None
