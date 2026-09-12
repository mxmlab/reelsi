#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""bench_vision.py — замер: чем описывать вставки (модель и промпт).

Задание EN. Код продакшна не трогает, insertlib.json только читает.
Результаты — в stdout (таблица, примеры, пути).

Использование::

    python tools/bench_vision.py          # из reelsi/
    python reelsi/tools/bench_vision.py   # из папки над reelsi/
"""
from __future__ import annotations

import builtins
import functools
import json
import os
import re
import sys
import time

# небуферизованный вывод — иначе при piped stdout результаты не видны в логе
print = functools.partial(builtins.print, flush=True)  # noqa: A001

# ── путь к корню reelsi (чтобы импортировать aicut и insertlib) ──
_HERE = os.path.dirname(os.path.abspath(__file__))
_REELSI = os.path.dirname(_HERE)
if _REELSI not in sys.path:
    sys.path.insert(0, _REELSI)

from core import aicut  # ensure_loaded
from core import insertlib  # _http_json, _thumb_b64, LMSTUDIO_URL, describe_file, _DESCRIBE_PROMPT

# Корень рабочих папок (родителя репозитория) задаёт REELSI_BENCH_ROOT; по умолчанию —
# рядом с репозиторием. Абсолютных личных путей в файле нет: сторож
# tests/test_public_clean.py не пропускает ни логин, ни имя рабочей папки.
BENCH_ROOT = os.environ.get("REELSI_BENCH_ROOT") or os.path.dirname(_REELSI)
# Индекс от корня рабочих папок, когда он задан; иначе — как insertlib (свой
# REELSI_INSERTLIB или insertlib.json рядом с модулем).
INDEX_PATH = insertlib.INDEX_PATH
if os.environ.get("REELSI_BENCH_ROOT"):
    INDEX_PATH = os.path.join(BENCH_ROOT, "reelsi", "insertlib.json")

# ── константы задания ──
EXCLUDE_WORDS = frozenset(
    "packaging medicine pharmacy bottle syringe injection capsule "
    "tablets tablet powder vitamin protein generated vecteezy".split()
)
MIN_KEYWORD_LEN = 8

# Промпт «только предмет»
NEW_PROMPT = (
    "Name the MAIN OBJECT in this image as precisely as you can, in 4-10 English words.\n"
    "Be specific: \"glucometer\", not \"device\"; \"anastrozole tablet box\", not \"packaging\".\n"
    "If the object has a brand or product name visible, include it.\n"
    "Do NOT mention style, colors, lighting or background. Output ONLY the object phrase."
)

# Прогоны: (id, модель, промпт)
RUNS: list[tuple[str, str, str]] = [
    ("P0", "google/gemma-4-e2b",                                insertlib._DESCRIBE_PROMPT),
    ("P1", "google/gemma-4-e2b",                                NEW_PROMPT),
    ("P2", "qwen2.5-omni-7b",                                   NEW_PROMPT),
    ("P3", "gemma4-e4b-sft-claude-opus-reasoning-unsloth",       NEW_PROMPT),
    ("P4", "qwen3-vl-8b-instruct-abliterated",                  NEW_PROMPT),
]


# ── оракул: отбор файлов ──

def _keyword(name: str) -> str | None:
    """Ключевое слово из имени файла (>= MIN_KEYWORD_LEN букв, не в стоп-листе,
    не начинается на 'gemini')."""
    base = os.path.splitext(name)[0]
    # разделители → пробелы
    tokens = re.split(r"[_\-\.\(\)\[\]{}+,\s]+", base.lower())
    for t in tokens:
        if len(t) < MIN_KEYWORD_LEN:
            continue
        if t in EXCLUDE_WORDS:
            continue
        if t.startswith("gemini"):
            continue
        # только латиница — имена файлов английские
        if re.fullmatch(r"[a-z]+", t):
            return t
    return None


def select_oracle(items: list[dict]) -> list[tuple[dict, str]]:
    """Записи-оракул: desc_src=='ai', gone нет, файл на диске, ключевое слово есть.
    Возвращает 40 штук: отсортировать по path, взять каждый 6-й."""
    pool = []
    for it in items:
        if it.get("desc_src") != "ai":
            continue
        if it.get("gone"):
            continue
        kw = _keyword(it.get("name", ""))
        if kw is None:
            continue
        if not os.path.isfile(it["path"]):
            continue
        pool.append((it, kw))
    pool.sort(key=lambda x: x[0]["path"])
    selected = pool[::6]  # каждый 6-й
    return selected[:40]


# ── vision-вызов с произвольным промптом ──

def _describe_with_prompt(path: str, model: str, prompt: str) -> str | None:
    """Отправляет картинку в LM Studio с указанным промптом. -> текст | None."""
    b64 = insertlib._thumb_b64(path)
    if not b64:
        return None
    try:
        d = insertlib._http_json(insertlib.LMSTUDIO_URL + "/chat/completions", {
            "model": model,
            "max_tokens": 900,
            "temperature": 0.2,
            "reasoning": {"enabled": False},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ]}],
        }, timeout=300)
        ch = ((d.get("choices") or [{}])[0])
        msg = (ch.get("message") or {})
        txt = msg.get("content") or ""
        if not txt.strip():
            return None
        txt = " ".join(txt.replace("\n", " ").split()).strip().strip(".'\"")
        return txt[:220] or None
    except Exception as exc:
        print(f"    ОШИБКА: {exc}", file=sys.stderr)
        return None


# ── метрика ──

def hit(keyword: str, response: str) -> bool:
    """Ключевое слово из имени встретилось в ответе (по подстроке, без регистра)."""
    return keyword.lower() in response.lower()


# ── главный замер ──

def run_bench(oracle: list[tuple[dict, str]]) -> dict[str, list[dict]]:
    """Прогон всех RUNS по oracle. Возвращает {run_id: [результаты]}."""
    all_results: dict[str, list[dict]] = {}

    for run_id, model, prompt in RUNS:
        print(f"\n{'='*60}")
        print(f"  Прогон {run_id}: модель={model}")
        print(f"{'='*60}")

        # загрузить модель (выгрузит предыдущую нашу)
        print(f"  Загрузка модели {model}...")
        try:
            aicut.ensure_loaded(model)
        except Exception as exc:
            print(f"  ⚠ ensure_loaded: {exc}")

        # ждём немного после загрузки — дать модели встать
        time.sleep(3)

        results = []
        for idx, (it, kw) in enumerate(oracle):
            t0 = time.monotonic()
            resp = _describe_with_prompt(it["path"], model, prompt)
            elapsed = time.monotonic() - t0
            resp_text = resp or "(пусто)"
            is_hit = hit(kw, resp_text) if resp else False
            results.append({
                "path": it["path"],
                "name": it["name"],
                "keyword": kw,
                "response": resp_text,
                "hit": is_hit,
                "words": len(resp_text.split()) if resp else 0,
                "seconds": round(elapsed, 2),
            })
            mark = "✓" if is_hit else "✗"
            print(f"  [{idx+1:2d}/40] {mark} {kw:20s} → {resp_text[:60]:<60s} ({elapsed:.1f}s)")

        all_results[run_id] = results
    return all_results


# ── отчёт ──

def print_report(oracle: list[tuple[dict, str]], all_results: dict[str, list[dict]]):
    print("\n" + "=" * 80)
    print("  ОТЧЁТ — замер EN: чем описывать вставки")
    print("=" * 80)

    # 1) Таблица
    print("\n## 1) Таблица результатов\n")
    print(f"{'Прогон':<8} {'Модель':<50} {'Попадания':>10} {'Ср.слов':>8} {'Сек/файл':>9}")
    print("-" * 87)
    for run_id, model, _prompt in RUNS:
        res = all_results[run_id]
        hits = sum(1 for r in res if r["hit"])
        total = len(res)
        avg_words = sum(r["words"] for r in res) / total if total else 0
        avg_sec = sum(r["seconds"] for r in res) / total if total else 0
        pct = hits / total * 100 if total else 0
        print(f"{run_id:<8} {model:<50} {hits}/{total} ({pct:4.0f}%) {avg_words:7.1f} {avg_sec:8.1f}")

    # 2) По 5 примеров
    print("\n## 2) Примеры (по 5 на прогон)\n")
    for run_id, model, _prompt in RUNS:
        print(f"\n### {run_id} — {model}\n")
        res = all_results[run_id]
        # берём первые 3 попадания и первые 2 промаха (или сколько есть)
        hits_ex = [r for r in res if r["hit"]][:3]
        miss_ex = [r for r in res if not r["hit"]][:2]
        examples = (hits_ex + miss_ex)[:5]
        if len(examples) < 5:
            # добить из оставшихся
            shown = {id(e) for e in examples}
            for r in res:
                if id(r) not in shown and len(examples) < 5:
                    examples.append(r)
                    shown.add(id(r))
        for r in examples:
            mark = "✓" if r["hit"] else "✗"
            print(f"  {mark} {r['keyword']:20s} → {r['response'][:80]}")

    # 3) Список 40 путей
    print("\n## 3) Список 40 путей оракула\n")
    for i, (it, kw) in enumerate(oracle):
        print(f"  {i+1:2d}. [{kw}] {it['path']}")

    print()


def main():
    # загружаем индекс (только чтение)
    index_path = INDEX_PATH
    print(f"Индекс: {index_path}")
    print(f"  размер: {os.path.getsize(index_path):,} байт")
    print(f"  mtime:  {time.ctime(os.path.getmtime(index_path))}")
    mtime_before = os.path.getmtime(index_path)
    size_before = os.path.getsize(index_path)

    with open(index_path, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", [])
    print(f"  записей: {len(items)}")

    # отбор оракула
    oracle = select_oracle(items)
    print(f"  оракул: {len(oracle)} файлов")
    if len(oracle) < 40:
        print(f"  ⚠ нашлось только {len(oracle)} файлов (ожидалось 40)")

    # замер
    all_results = run_bench(oracle)

    # отчёт
    print_report(oracle, all_results)

    # 5) проверка insertlib.json
    mtime_after = os.path.getmtime(index_path)
    size_after = os.path.getsize(index_path)
    print("## 5) insertlib.json: до и после\n")
    print(f"  ДО:    размер={size_before:,}  mtime={time.ctime(mtime_before)}")
    print(f"  ПОСЛЕ: размер={size_after:,}  mtime={time.ctime(mtime_after)}")
    if size_before == size_after and mtime_before == mtime_after:
        print("  ✓ Не изменился")
    else:
        print("  ✗ ИЗМЕНИЛСЯ! Это ошибка.")


if __name__ == "__main__":
    main()
