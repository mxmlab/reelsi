# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Стенд сравнения стоков (`tools/stock_compare.py`).

Проверяем то, ради чего стенд и написан: по ОДНОМУ запросу видно выдачу всех
провайдеров в одной странице (колонка на сток), у каждой строки есть упрощённый
вариант запроса, и в превью уходят ссылки провайдера, а не скачанные файлы.

Сети нет: поиск провайдера подменён записанным ответом (фейк ПО URL — как в
tests/test_stock.py), а страница собирается чистой функцией, которую можно позвать
на готовых данных.

Запуск: py -3.10 -m pytest tests/test_stock_compare.py -q
"""
import json
import os
import sys
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import stock_compare  # noqa: E402
from core import stock  # noqa: E402


@pytest.fixture(autouse=True)
def _cache_in_tmp(tmp_path, monkeypatch):
    """Кеш стока — во временной папке. Без этого тест CLI писал выдачу-заглушку в
    боевой `stock_cache.json` корня репозитория, а там кеш живёт для реального поиска."""
    monkeypatch.setattr(stock, "CACHE_PATH", str(tmp_path / "stock_cache.json"))
    monkeypatch.setenv("REELSI_STOCK_CACHE", str(tmp_path / "stock_cache.json"))


def _cand(provider: str, i: int) -> dict:
    return {"provider": provider, "id": f"{provider}-{i}", "kind": "photo",
            "thumb": f"https://cdn.{provider}.test/{i}-thumb.jpg",
            "page_url": f"https://{provider}.test/photo/{i}",
            "download_url": f"https://cdn.{provider}.test/{i}.jpg",
            "author": f"Автор {provider} {i}", "query": "cat on the window"}


def _cell(provider: str, query: str, label: str, count: int = 2) -> dict:
    """Ячейка стенда в том виде, в каком её отдаёт `collect`."""
    return {"query": query, "provider": provider, "kind": "photo", "label": label,
            "results": [_cand(provider, i) for i in range(1, count + 1)], "error": ""}


def _rows() -> list:
    """Строка стенда, как её собирает `collect`: два варианта запроса на провайдера."""
    cells = []
    for prov in ("pexels", "openverse"):
        cells.append(_cell(prov, "cat on the window", ""))
        cells.append(_cell(prov, "cat window", "короткий", count=1))
    return [{"query": "cat on the window", "simple": "cat window", "cells": cells}]


def test_html_has_provider_columns_and_previews():
    """В странице есть колонка на провайдера, превью-ссылки и подпись короткого варианта."""
    html = stock_compare.build_html(_rows(), "photo", ("pexels", "openverse"))
    assert "<th>pexels</th>" in html and "<th>openverse</th>" in html
    assert "https://cdn.pexels.test/1-thumb.jpg" in html          # ссылка на превью
    assert "https://pexels.test/photo/1" in html                  # ведёт на страницу кадра
    assert html.count(">короткий<") == 2                          # по подписи на провайдера
    assert "cat window" in html and "cat on the window" in html   # обе версии запроса
    assert "Автор pexels 1" in html                               # атрибуция в подписи
    assert "<img" in html and "loading=\"lazy\"" in html


def test_html_drops_non_http_links():
    """Адрес от стока в href/src — только http(s): `javascript:` на локальной странице
    не выводим, ссылка на кадр тогда падает на превью."""
    rows = [{"query": "cat", "simple": "cat", "cells": [
        {"provider": "pexels", "query": "cat", "label": "", "error": "", "results": [
            {"provider": "pexels", "id": 1, "kind": "photo", "thumb": "javascript:alert(1)",
             "page_url": "javascript:alert(2)", "author": "A"}]}]}]
    html = stock_compare.build_html(rows, "photo", ("pexels",))
    assert "javascript:" not in html
    assert "<span class=\"noimg\"></span>" in html


def test_collect_asks_for_full_count_not_preview_count(monkeypatch):
    """Счётчик «найдено» — по выдаче, а не по четырём превью: в `main` стенд просит
    `SEARCH_N` кадров у каждого стока."""
    assert stock_compare.SEARCH_N > stock_compare.PREVIEWS
    asked: list[int] = []

    def fake(provider, query, kind, n, *a, **k):
        asked.append(n)
        return {"query": query, "provider": provider, "kind": kind, "results": [],
                "error": "", "label": ""}

    monkeypatch.setattr(stock_compare, "search_provider", fake)
    monkeypatch.setattr(stock, "provider_ready", lambda p: True)
    stock_compare.collect(["cat"], "photo", stock_compare.SEARCH_N, ("pexels",))
    assert asked and set(asked) == {stock_compare.SEARCH_N}


def test_html_marks_provider_without_key_and_empty_result():
    """«Нет ключа» и «ничего» — разные подписи: на стенде это разные диагнозы."""
    rows = [{"query": "cat", "simple": "cat", "cells": [
        {"provider": "pexels", "query": "cat", "results": [], "error": "нет ключа",
         "label": ""},
        {"provider": "openverse", "query": "cat", "results": [], "error": "", "label": ""},
    ]}]
    html = stock_compare.build_html(rows, "photo", ("pexels", "openverse"))
    assert "— нет ключа" in html and "— ничего" in html


class _Resp:
    """Ответ фейковой сети: тело отдаём целиком (json.load читает его без аргумента)."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, n: int | None = None) -> bytes:
        return self._data if n is None else self._data[:n]

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_collect_queries_search_and_simplify(monkeypatch):
    """`collect` ищет КАЖДОЙ готовой колонкой провайдера и кладёт оба варианта запроса.

    Поиск подменён: проверяется раскладка строки стенда (обычный запрос + упрощённый),
    а не сам разбор ответов — тот проверяется в tests/test_stock.py."""
    seen: list[tuple] = []

    def fake_provider_search(provider, key, query, kind, n, size, orient):
        seen.append((provider, query, kind))
        return [{**_cand(provider, 1), "query": query}]

    monkeypatch.setattr(stock, "_provider_search", fake_provider_search)
    monkeypatch.setattr(stock, "provider_ready", lambda prov: True)

    rows = stock_compare.collect(["cat on the window"], "photo", 4,
                                 ("pexels", "openverse"))

    assert len(rows) == 1 and rows[0]["simple"] == "cat window"
    labels = [(c["provider"], c["query"], c["label"]) for c in rows[0]["cells"]]
    assert labels == [("pexels", "cat on the window", ""),
                      ("pexels", "cat window", "короткий"),
                      ("openverse", "cat on the window", ""),
                      ("openverse", "cat window", "короткий")]
    assert ("pexels", "cat window", "photo") in seen


def test_collect_skips_providers_without_key(monkeypatch):
    """Провайдер без ключа не ищет вовсе, а показывается подписью «нет ключа»."""
    monkeypatch.setattr(stock, "provider_ready", lambda prov: prov == "openverse")
    monkeypatch.setattr(stock, "_provider_search",
                        lambda *a, **k: [_cand("openverse", 1)])

    rows = stock_compare.collect(["cat"], "photo", 4, ("pexels", "openverse"))
    by_prov = {c["provider"]: c for c in rows[0]["cells"]}
    assert by_prov["pexels"]["error"] == "нет ключа или выключен" and by_prov["pexels"]["results"] == []
    assert by_prov["openverse"]["results"]


def test_queries_from_inserts_json(tmp_path):
    """Запросы ИИ-вставок берутся из `<stem>.inserts.json` — поле `query`, до лимита."""
    side = tmp_path / "01_C100.inserts.json"
    side.write_text(json.dumps({"inserts": [
        {"query": "cat on the window", "type": "photo"},
        {"query": "clean energy", "type": "photo"},
        {"query": "cat on the window", "type": "photo"},      # дубль — один раз
        {"type": "video"},                                    # без запроса — мимо
    ]}, ensure_ascii=False), encoding="utf-8")

    assert stock_compare.queries_from_inserts_json(str(side)) == ["cat on the window",
                                                                 "clean energy"]
    assert stock_compare.queries_from_inserts_json(str(side), limit=1) == ["cat on the window"]
    # битый или отсутствующий сайдкар — не повод падать
    broken = tmp_path / "broken.inserts.json"
    broken.write_text("{не json", encoding="utf-8")
    assert stock_compare.queries_from_inserts_json(str(broken)) == []
    assert stock_compare.queries_from_inserts_json(str(tmp_path / "нет.json")) == []


def test_queries_from_dirs(tmp_path):
    """Папка выходов спикера: сайдкары собираются рекурсивно, порядок и лимит соблюдены."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "01.inserts.json").write_text(
        json.dumps({"inserts": [{"query": "a"}, {"query": "b"}]}), encoding="utf-8")
    (tmp_path / "sub" / "02.inserts.json").write_text(
        json.dumps({"inserts": [{"query": "c"}]}), encoding="utf-8")

    got = stock_compare.queries_from_dirs([str(tmp_path)], limit=12)
    assert sorted(got) == ["a", "b", "c"]
    assert stock_compare.queries_from_dirs([str(tmp_path)], limit=2) == got[:2]


def test_write_html_and_cli_without_queries(tmp_path, capsys):
    """Страница пишется файлом; без запросов CLI говорит об этом и ничего не делает."""
    path = stock_compare.write_html(str(tmp_path / "c.html"),
                                    stock_compare.build_html(_rows(), "photo",
                                                             ("pexels", "openverse")))
    assert os.path.isfile(path) and "<html" in open(path, encoding="utf-8").read()

    assert stock_compare.main([]) == 2
    assert "нет запросов" in capsys.readouterr().err


def test_cli_builds_page_from_recorded_answers(tmp_path, monkeypatch):
    """CLI целиком: запрос аргументом, поиск на записанных ответах, одна страница на выходе.

    Сеть подменена ПО URL (как в tests/test_stock.py): не описанный адрес — ошибка,
    а не выход в интернет. Ключи — стендовые, кеш и конфиг изолирует autouse-фикстура.
    """
    from core.aicut import config as aicut_config
    with open(aicut_config.AI_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"active": "Стенд", "profiles": {"Стенд": {
            "provider": "lmstudio", "base_url": "http://localhost:1234/v1",
            "api_key": "", "model": "стенд"}},
            "stock": {"pexels_key": "PK-1"}}, f, ensure_ascii=False)
    answers = {"api.pexels.com/v1/search": {"photos": [
        {"id": 1, "width": 1080, "height": 1920,
         "url": "https://www.pexels.com/photo/1/", "photographer": "Автор П",
         "photographer_url": "https://www.pexels.com/@p", "alt": "cat",
         "src": {"large2x": "https://images.pexels.com/photos/1/large2x.jpg",
                 "medium": "https://images.pexels.com/photos/1/medium.jpg"}}]}}

    def fake_urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        for frag, payload in answers.items():
            if frag in url:
                return _Resp(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        raise AssertionError("сеть в тестах запрещена: " + url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(stock, "unsafe_url_reason", lambda url: "")
    monkeypatch.setenv("REELSI_STOCK_OFF", "openverse,coverr")

    out = tmp_path / "compare.html"
    code = stock_compare.main(["cat on the window", "-o", str(out), "--provider", "pexels"])
    assert code == 0 and out.is_file()
    html = out.read_text(encoding="utf-8")
    assert "cat on the window" in html and "cat window" in html
    assert "images.pexels.com/photos/1/medium.jpg" in html     # превью, не оригинал
    assert "large2x" not in html                               # оригинал стенд не качает


def test_write_html_creates_missing_folder(tmp_path):
    """`-o папка/compare.html` в ещё не существующей папке: страница пишется, папка создаётся.
    Раньше падало FileNotFoundError уже после поиска по стокам."""
    target = tmp_path / "новая папка" / "вложенная" / "compare.html"
    path = stock_compare.write_html(str(target), "<html>ok</html>")
    assert os.path.isfile(path)
    assert open(path, encoding="utf-8").read() == "<html>ok</html>"


def test_help_does_not_crash_on_cp1252_console():
    """`--help` с русской справкой в консоли cp1252 (Windows по умолчанию) не падает
    UnicodeEncodeError: поток переводится в utf-8 в начале main. Подпроцесс с
    PYTHONIOENCODING=cp1252 воспроизводит такую консоль на любой ОС. Сети нет:
    до разбора аргументов скрипт ничего не трогает."""
    import subprocess
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    script = os.path.join(ROOT, "tools", "stock_compare.py")
    r = subprocess.run([sys.executable, script, "--help"], capture_output=True,
                       env=env, timeout=120)
    err = r.stderr.decode("utf-8", "replace")
    assert r.returncode == 0, err
    assert "UnicodeEncodeError" not in err
    assert "Сравнить выдачу стоков" in r.stdout.decode("utf-8")
