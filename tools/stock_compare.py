# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""tools/stock_compare.py — стенд сравнения стоков: один запрос — все провайдеры.

Зачем он есть. Запросы ИИ-вставок («concept of a copper pot on a wooden table») на
стоке ищутся плохо: длинная фраза у стока матчится по любому слову, и в выдачу лезет
мусор. Провайдеров теперь пять, у каждого своя выдача, и «мало всего» — это претензия
к КОНКРЕТНОМУ запросу у КОНКРЕТНОГО стока. Глазами, по одному поиску на карточке, это
не сравнить: стенд показывает всё сразу в одной странице.

    python tools/stock_compare.py "cat on the window" "copper pot"
    python tools/stock_compare.py --file queries.txt -o compare.html
    python tools/stock_compare.py --inserts-dir Reelsi_out --limit 12
    python tools/stock_compare.py --dirs Reelsi_out/01 Reelsi_out/02 --type video

Каждая строка — запрос; в строке две версии: как её дала модель и короткая
(`core.stock.simplify_query`: предмет + 1–2 признака). Дальше по колонке на провайдера
с первыми превью. Так на одной странице видно, какой запрос и какой сток дают попадание.

Что стенд НЕ делает нарочно:

- **не пишет в базу вставок** — иначе сравнение задним числом меняло бы боевой подбор;
- **не качает оригиналы** — только превью-ссылки провайдера (кеш ответов поиска
  `core.stock.CACHE_PATH` стенд всё же использует: он не даёт выжечь чужие лимиты
  запросов и лежит в том же месте, что у боевого поиска);
- **не отправляет «упрощённый» запрос в бой** — правило в ядре есть, но поиск карточки
  его не зовёт: включение — отдельное решение владельца.
"""
import argparse
import html
import json
import os
import sys
import webbrowser
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import stock                                        # noqa: E402
from core.umsg import ReelsiError                             # noqa: E402

# Сколько превью показываем в колонке: этого хватает, чтобы увидеть «попал / не попал»,
# и страница остаётся читаемой при пяти провайдерах.
PREVIEWS = 4
# Сколько кадров просим у каждого стока: счётчик «найдено» должен считать выдачу, а не
# обрезанные превью (поиск отдаёт не больше `n`, поэтому n — заметно больше PREVIEWS).
SEARCH_N = 20
DEFAULT_LIMIT = 12
DEFAULT_OUT = "stock_compare.html"


def read_query_file(path: str) -> list[str]:
    """Запросы из файла: строка = запрос, `#` — комментарий. Пустые строки пропускаем."""
    out: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            q = line.strip()
            if q and not q.startswith("#"):
                out.append(q)
    return out


def queries_from_inserts_json(path: str, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Запросы ИИ-вставок из `<stem>.inserts.json` (до `limit` штук, по порядку).

    Файла нет или он битый — пустой список: стенд не должен падать от чужого
    сайдкара, которого могло не остаться. Запрос в разметке — поле `query`."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    items = data.get("inserts") if isinstance(data, dict) else None
    out: list[str] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        q = str(it.get("query") or "").strip()
        if q and q not in out:
            out.append(q)
        if len(out) >= max(1, int(limit)):
            break
    return out


def queries_from_dirs(dirs: list[str], limit: int = DEFAULT_LIMIT) -> list[str]:
    """Запросы из папок спикеров/роликов: `<stem>.inserts.json` в каждой (рекурсивно)."""
    out: list[str] = []
    for d in dirs:
        for root, _sub, files in os.walk(d):
            for name in sorted(files):
                if not name.endswith(".inserts.json"):
                    continue
                for q in queries_from_inserts_json(os.path.join(root, name), limit):
                    if q not in out:
                        out.append(q)
    return out[:max(1, int(limit))] if out else []


def search_provider(provider: str, query: str, kind: str, n: int) -> dict[str, Any]:
    """Поиск у ОДНОГО провайдера: кандидаты или текст ошибки.

    Ошибку не глотаем и не превращаем в пустоту: «пусто» и «401» — разные диагнозы,
    и на стенде это как раз главное, что нужно увидеть. Ключа нет — так и пишем, а не
    выдаём это за «ничего не нашлось»."""
    key = stock.keys().get(provider) or ""
    try:
        cands = stock._provider_search(provider, key, query, kind, n, stock._target_size(None),
                                       "portrait")
        return {"query": query, "provider": provider, "kind": kind,
                "results": cands, "error": ""}
    except ReelsiError as e:
        return {"query": query, "provider": provider, "kind": kind,
                "results": [], "error": str(e)}
    except Exception as e:
        return {"query": query, "provider": provider, "kind": kind,
                "results": [], "error": f"{type(e).__name__}: {e}"}


def collect(queries: list[str], kind: str, n: int = PREVIEWS,
            providers: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """Результаты по всем запросам и провайдерам: строка на запрос.

    Каждый запрос ищется ДВАЖДЫ — как есть и в коротком виде: сравнение запросов и
    есть смысл стенда. Упрощение берётся из ядра (`stock.simplify_query`), а не
    живёт копией здесь: включать его в бой будут именно там. У ячейки варианта есть
    `label` — «упрощённый» только там, где вариант действительно другой."""
    provs = providers if providers is not None else stock.PROVIDERS
    rows: list[dict[str, Any]] = []
    for q in queries:
        simple = stock.simplify_query(q)
        variants = [q] if simple == q else [q, simple]
        cells: list[dict[str, Any]] = []
        for p in provs:
            if kind not in stock.PROVIDER_META.get(p, {}).get("kinds", ()):
                cells.append({"query": q, "provider": p, "kind": kind, "results": [],
                              "error": "не умеет этот тип", "label": ""})
                continue
            if not stock.provider_ready(p):
                cells.append({"query": q, "provider": p, "kind": kind, "results": [],
                              "error": "нет ключа или выключен", "label": ""})
                continue
            for i, var in enumerate(variants):
                cell = search_provider(p, var, kind, n)
                cell["label"] = "короткий" if i else ""
                cells.append(cell)
        rows.append({"query": q, "simple": simple, "cells": cells})
    return rows


def _safe_url(u: str) -> str:
    """Ссылка в страницу — только http(s): адрес приходит от стока, а не от нас, и
    `javascript:` в href на локальной странице был бы ровно тем, чего тут быть не должно."""
    u = str(u or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else ""


def _found(cells: list[dict[str, Any]], provider: str) -> int:
    """Сколько кадров нашлось у провайдера во всех строках (для сводки под заголовком)."""
    return sum(len(c.get("results") or []) for c in cells if c.get("provider") == provider)


def _variants_html(variants: list[dict[str, Any]]) -> str:
    """Одна колонка провайдера: выдача обычного запроса и (ниже) упрощённого.

    Два варианта в одной колонке, а не в двух: строка остаётся «запрос — провайдеры»,
    и разница видна глазами сверху вниз. Подпись «упрощённый» ставится только там,
    где вариант действительно другой — иначе две одинаковые картинки читаются как
    разные стоки."""
    if not variants:
        return ""
    blocks = []
    for v in variants:
        items = [c for c in v.get("results") or [] if isinstance(c, dict)]
        label = str(v.get("label") or "")
        head = f'<div class="simp">{html.escape(label)}</div>' if label else ""
        if v.get("error"):
            blocks.append(f'<div class="v">{head}<div class="none">— '
                          f'{html.escape(str(v["error"]))}</div></div>')
            continue
        if not items:
            blocks.append(f'<div class="v">{head}<div class="none">— ничего</div></div>')
            continue
        imgs = []
        for c in items[:PREVIEWS]:
            thumb = _safe_url(c.get("thumb") or "")
            page = _safe_url(c.get("page_url") or "") or thumb
            who = " · ".join(x for x in (str(c.get("provider") or ""),
                                         str(c.get("author") or "")) if x)
            img = (f'<img loading="lazy" src="{html.escape(thumb, quote=True)}" alt="">'
                   if thumb else '<span class="noimg"></span>')
            imgs.append(f'<a class="prev" href="{html.escape(page, quote=True)}" '
                        f'title="{html.escape(who, quote=True)}">{img}'
                        f'<span class="who">{html.escape(who)}</span></a>')
        blocks.append(f'<div class="v">{head}<div class="prevs">{"".join(imgs)}</div>'
                      f'<div class="cnt">{len(items)}</div></div>')
    return "".join(blocks)


def build_html(rows: list[dict[str, Any]], kind: str,
               providers: tuple[str, ...] | None = None) -> str:
    """Собрать ОДНУ страницу стенда: строка на запрос, колонка на провайдера.

    Ссылки на превью ведут на страницу кадра у провайдера: полноразмерные файлы стенд
    не качает — иначе сравнение десяти запросов по пяти стокам качало бы сотни
    мегабайт и жгло чужие лимиты."""
    provs = providers if providers is not None else stock.PROVIDERS
    head = "".join(f"<th>{html.escape(p)}</th>" for p in provs)
    body = []
    for row in rows:
        q, simple = str(row.get("query") or ""), str(row.get("simple") or "")
        cells = [c for c in row.get("cells") or [] if isinstance(c, dict)]
        by_prov: dict[str, list[dict[str, Any]]] = {}
        for c in cells:
            by_prov.setdefault(str(c.get("provider") or ""), []).append(c)
        tds = [f'<td class="cell">{_variants_html(by_prov.get(p) or [])}</td>' for p in provs]
        body.append(f'<tr><th class="q"><div class="qmain">{html.escape(q)}</div>'
                    f'<div class="qsim">{html.escape(simple)}</div></th>{"".join(tds)}</tr>')
    sub = " · ".join(f"{html.escape(p)}: {_found([c for r in rows for c in r['cells']], p)} кадров"
                     for p in provs)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Reelsi — сравнение стоков</title>
<style>
body{{background:#101010;color:#e8e8e8;font:13px/1.4 system-ui,Segoe UI,sans-serif;margin:16px}}
h1{{font-size:15px;font-weight:400;text-transform:uppercase;letter-spacing:.04em;margin:0 0 4px}}
.sub{{color:#8a8a8a;font-size:12px;margin-bottom:14px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #212121;padding:6px 8px;vertical-align:top}}
th{{background:#161616;font-weight:400;text-align:left}}
th.q{{width:200px}}
.qmain{{color:#f5c518}}
.qsim{{color:#8a8a8a;font-size:12px;margin-top:3px}}
.cell{{min-width:150px}}
.cell.err .none{{color:#ff6b6b}}
.none{{color:#6a6a6a}}
.v+.v{{margin-top:6px;padding-top:6px;border-top:1px solid #1c1c1c}}
.simp{{color:#8a8a8a;font-size:11px;margin-bottom:4px}}
.prevs{{display:flex;flex-wrap:wrap;gap:4px}}
.prev{{display:block;width:72px;text-decoration:none;color:#8a8a8a}}
.prev img{{width:72px;height:48px;object-fit:cover;border-radius:4px;display:block}}
.who{{font-size:10px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.cnt{{color:#98ff38;font-size:11px;margin-top:3px}}
.noimg{{display:block;width:72px;height:48px;background:#1c1c1c;border-radius:4px}}
.type{{color:#8a8a8a}}
</style></head><body>
<h1>Сравнение стоков</h1>
<div class="sub">тип: <span class="type">{html.escape(kind)}</span> · {sub}<br>
строка = запрос карточки; под ним — его короткий вариант. В колонке провайдера: сверху
выдача обычного запроса, ниже (с подписью «короткий») — упрощённого. Показаны первые {PREVIEWS} превью.</div>
<table><thead><tr><th class="q">запрос</th>{head}</tr></thead>
<tbody>{"".join(body)}</tbody></table>
</body></html>
"""


def write_html(path: str, text: str) -> str:
    """Записать страницу (UTF-8, LF) и вернуть путь.

    Папку создаём сами: `-o out/compare.html` в ещё не существующей папке раньше падал
    FileNotFoundError уже после поиска по всем стокам — кандидаты пропадали впустую."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return os.path.abspath(path)


def main(argv: list[str] | None = None) -> int:
    # Консоль Windows по умолчанию cp1252: русская справка (--help) падала
    # UnicodeEncodeError. Переводим потоки в utf-8 до первого вывода.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass  # поток без поддержки кодировки (тестовый перехват) — как есть
    ap = argparse.ArgumentParser(description="Сравнить выдачу стоков по запросам вставок")
    ap.add_argument("queries", nargs="*", help="запросы (как есть)")
    ap.add_argument("--file", help="файл со запросами: строка = запрос, # — комментарий")
    ap.add_argument("--dirs", nargs="*", default=[], help="папки с <stem>.inserts.json")
    ap.add_argument("--inserts-dir", action="append", default=[],
                    help="папка выходов спикера: взять запросы ИИ-вставок")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="сколько запросов брать")
    ap.add_argument("--type", default="photo", choices=list(stock.KINDS), help="тип стока")
    ap.add_argument("--provider", action="append", default=[],
                    help="только эти стоки (можно несколько раз)")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT, help="куда писать HTML")
    ap.add_argument("--open", action="store_true", help="открыть страницу в браузере")
    args = ap.parse_args(argv)

    queries: list[str] = list(args.queries)
    if args.file:
        queries += read_query_file(args.file)
    if args.inserts_dir:
        queries += queries_from_dirs(list(args.inserts_dir), args.limit)
    if args.dirs:
        queries += queries_from_dirs(list(args.dirs), args.limit)
    seen: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.append(q)
    queries = seen[:max(1, int(args.limit))]
    if not queries:
        print("нет запросов: передай их аргументами, в --file или через --inserts-dir",
              file=sys.stderr)
        return 2

    providers = tuple(args.provider) if args.provider else stock.PROVIDERS
    rows = collect(queries, args.type, SEARCH_N, providers)
    path = write_html(args.out, build_html(rows, args.type, providers))
    total = sum(len(c.get("results") or []) for r in rows for c in r["cells"])
    print(f"запросов: {len(rows)} · кандидатов: {total} -> {path}")
    print("стоки: " + ", ".join(f"{p}{'' if stock.provider_ready(p) else ' (нет ключа)'}"
                                for p in providers))
    if args.open:
        webbrowser.open(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
