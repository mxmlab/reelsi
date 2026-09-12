# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Задание GZ, пункт A: файлы состояния пишутся атомарно (`core/fileio.py`).

`json.dump(obj, open(path, "w"))` усекает файл ДО сериализации: крах, отбой питания
или «Стоп» в этот момент оставляют пустой либо битый JSON, а данные эти ниоткуда не
пересобираются (project.json, .cuts.json, ai_config.json, индексы, стили).

Два сторожа класса:
  * статический скан `core/` и `api/` — новых неатомарных записей быть не должно
    (кэш и временные файлы перечислены поимённо: fileio.py сам оговаривает, что
    паттерн нужен данным, а не пересоздаваемому кэшу);
  * живая проверка: две параллельные записи из потоков дают валидный JSON одного из
    вариантов, а не смесь половин (у общего tmp-имени `AI_CONFIG_PATH + ".tmp"`).

Запуск:  python -m pytest tests -q
"""
import ast
import json
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# Места, где `json.dump(..., open(...))` пишет ПЕРЕСОЗДАВАЕМЫЙ кэш, временный файл
# или библиотеку, которая собирается заново из эталона: атомарность им не нужна
# (потеря не страшна). Ключ — путь от корня, значение — {первая строка инструкции:
# почему это не состояние}. Всё остальное обязано идти через atomic_json_dump.
CACHE_WRITES = {
    "core/ctc_asr.py": {
        'json.dump(ws, open(out, "w", encoding="utf-8"), ensure_ascii=False)':
            "кэш расшифровки CTC — следующий прогон перезапишет",
    },
    "core/falign_cli.py": {
        'json.dump(out, open(fout, "w", encoding="utf-8"), ensure_ascii=False)':
            "выход CLI-сабпроцесса во временный файл (путь задаёт вызывающий)",
    },
    "core/gigaam_cut/tune.py": {
        'json.dump(show, open(os.path.splitext(out)[0] + ".breaths.json", "w",':
            "сайдкар вздохов для редактора — пересобирается тем же прогоном",
    },
    "core/omni_asr.py": {
        'json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)':
            "кэш расшифровки Omni рядом с роликом",
    },
    "core/omni_cut.py": {
        'json.dump(sorted(new), open(HALLUC_PHRASES_PATH, "w", encoding="utf-8"),':
            "выученный список фраз-галлюцинаций — набирается заново прогонами",
        'json.dump([list(s) for s in spans if s], open(ivp, "w"))':
            "временный файл интервалов речека в рабочем каталоге нарезки",
        'json.dump([[s, e] for s, e in wins], open(ivf, "w"))':
            "временный файл интервалов full-анализа в рабочем каталоге",
        'json.dump([[s, e] for s, e in intervals], open(ivf, "w"))':
            "временный файл интервалов VAD в рабочем каталоге",
    },
    "core/omni_review.py": {
        'json.dump(notes, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)':
            "отчёт экспериментального ревью — пересобирается повторным прогоном",
    },
    "core/subtitle_blobs.py": {
        'json.dump({str(k): base64.b64encode(v).decode() for k, v in self.by_len.items()},':
            "библиотека блобов refblobs*.json — собирается заново из эталонного XML",
    },
    "core/transcribe.py": {
        'json.dump(ws, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)':
            "кэш пословного транскрипта",
    },
    "api/files.py": {
        'json.dump(res, open(cache, "w", encoding="utf-8"))':
            "кэш волны для превью",
    },
}


def _json_dump_open_lines(path):
    """Номера строк и тексты инструкций `json.dump(..., open(..., "w"))` в файле.

    Разбор через ast, а не регуляркой: инструкция бывает многострочной, а `open(`
    внутри аргумента виден только по дереву (`json.dump(sorted(new), open(...))`).
    Запись через `with open(path, "w") as f: json.dump(obj, f)` сюда не попадает.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "dump"
                and isinstance(fn.value, ast.Name) and fn.value.id == "json"):
            continue
        opened = any(isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                     and sub.func.id == "open"
                     for arg in node.args for sub in ast.walk(arg))
        if opened:
            out.append((node.lineno, " ".join(lines[node.lineno - 1].split())))
    return out


def test_нет_неатомарной_записи_состояния():
    """Класс целиком: в `core/` и `api/` не должно остаться `json.dump(..., open(...))`,
    кроме поимённо перечисленных кэшей. Новое место — падение сторожа."""
    found = {}
    for base in ("core", "api"):
        for p in sorted((ROOT / base).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            for lineno, text in _json_dump_open_lines(p):
                found.setdefault(rel, {})[text] = lineno
    new = []
    for rel, items in found.items():
        allowed = CACHE_WRITES.get(rel, {})
        for text, lineno in items.items():
            if text not in allowed:
                new.append(f"{rel}:{lineno}: {text}")
    assert not new, ("неатомарная запись состояния (переведи на core.fileio."
                     "atomic_json_dump):\n  " + "\n  ".join(sorted(new)))


def test_список_кэшей_не_стареет():
    """Строку починили — её надо убрать из списка: иначе он тихо врёт и прячет
    настоящую проверку (как сторож осиротевших ERR_-кодов в test_i18n)."""
    stale = []
    for rel, allowed in CACHE_WRITES.items():
        p = ROOT / rel
        assert p.is_file(), f"{rel} пропал"
        texts = {text for _, text in _json_dump_open_lines(p)}
        for text in allowed:
            if text not in texts:
                stale.append(f"{rel}: {text}")
    assert not stale, ("запись починена или переписана — убери из CACHE_WRITES:\n  "
                       + "\n  ".join(stale))


class _SlowJson:
    """json с «медленным» dump: пишет половину, отдаёт поток, дописывает остаток.

    Без этого окно гонки двух записей в ОДИН tmp-файл (`AI_CONFIG_PATH + ".tmp"`)
    попадается не каждый раз: нужно, чтобы второй писатель успел открыть файл,
    пока первый ещё не закрыл свой дескриптор.
    """

    def __init__(self):
        self._real = json

    def __getattr__(self, name):
        return getattr(self._real, name)

    def dump(self, obj, f, **kw):
        s = self._real.dumps(obj, **kw)
        half = len(s) // 2
        f.write(s[:half])
        f.flush()
        time.sleep(0.002)
        f.write(s[half:])


def test_параллельные_записи_конфига_дают_валидный_json(tmp_path, monkeypatch):
    """`save_ai_config` пишут два потока (api/ai.py и core/aicut/video.py). Общее
    tmp-имя без замка: писатели перемешивали половины, а второй os.replace падал на
    уже переименованном файле. У atomic_json_dump tmp уникальный — файл всегда
    равен одному из вариантов."""
    from core.aicut import config as C

    path = tmp_path / "ai_config.json"
    monkeypatch.setattr(C, "AI_CONFIG_PATH", str(path))
    monkeypatch.setattr(C, "json", _SlowJson())

    variants = [{"profiles": {f"p{k}": {"name": "провайдер", "key": "k" * 300, "n": k}}}
                for k in range(6)]
    errs = []
    start = threading.Barrier(len(variants))

    def worker(cfg):
        try:
            start.wait(timeout=10)
            for _ in range(10):
                C.save_ai_config(cfg)
        except Exception as e:                       # noqa: BLE001 — текст уйдёт в assert
            errs.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker, args=(v,)) for v in variants]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errs, f"запись упала: {errs[:3]}"
    got = json.loads(path.read_text(encoding="utf-8"))      # битый JSON — исключение
    assert got in variants, "в файле смесь двух записей"


def test_правка_списка_цензуры_не_убивает_файл_при_сбое(tmp_path, monkeypatch):
    """`censor.write_text` переписывал список пользователя через `open(..., "w")`:
    падение (или «Стоп») между усечением и записью оставляло ПУСТОЙ список, а пустой
    список в UI — это «ничего не цензурим». Пишем в tmp рядом и подменяем."""
    from core import censor

    bad = tmp_path / "badwords.user.txt"
    bad.write_text("старое\n", encoding="utf-8")
    monkeypatch.setitem(censor.USER_PATHS, "bad", str(bad))

    def boom(src, dst, *a, **k):
        raise OSError("сбой на подмене файла")

    monkeypatch.setattr(censor.os, "replace", boom)
    with pytest.raises(OSError):
        censor.write_text("bad", "новое слово")

    assert bad.read_text(encoding="utf-8") == "старое\n", "старый список затёрт"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["badwords.user.txt"], \
        "временный файл остался лежать"
