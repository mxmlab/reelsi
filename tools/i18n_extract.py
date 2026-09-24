# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
# -*- coding: utf-8 -*-
"""Собрать строки интерфейса для перевода.

    python reelsi/tools/i18n_extract.py            # показать, чего не хватает в en.json
    python reelsi/tools/i18n_extract.py --write    # дописать недостающие ключи пустыми

Ключ словаря — сам русский текст, как в gettext. Это осознанно:

- разметка и код не переписываются под ключи, то есть русский интерфейс
  продолжает работать вообще без словаря (fallback = ключ);
- не нужно придумывать тысячу имён вида `cut.speaker.label` и следить за ними;
- нет состояния «ключ есть, перевода нет» с пустой дыркой на экране.

Плата: правка русского текста рвёт связь с переводом. Для проекта, где русский —
исходный язык, а английский догоняющий, это правильный размен: заметно сразу
(строка вернулась на русский), чинится дописыванием ключа.

Что вытаскивается из `templates/index.html`:
  - видимый текст элементов,
  - `data-t` (тултипы «!»), `placeholder`, `title`, `aria-label`.

JS-строки этот скрипт НЕ трогает: там текст перемешан с конкатенацией и HTML,
и надёжно отличить сообщение от куска разметки регуляркой нельзя. Динамический
интерфейс переводится в рантайне (см. `applyI18n` в app.js).
"""
import json
import os
import re
import sys
from html.parser import HTMLParser
from typing import Any, cast

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HTML = os.path.join(ROOT, "templates", "index.html")
EN = os.path.join(ROOT, "static", "i18n", "en.json")

ATTRS = ("data-t", "placeholder", "title", "aria-label")
SKIP_TAGS = {"script", "style"}
CYR = re.compile(r"[А-Яа-яЁё]")


class Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip += 1
        for name, val in attrs:
            if name in ATTRS and val and CYR.search(val):
                self.found.append(val.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        # Плейсхолдеры шаблона (__BASE_JSON__) и куски кода сюда попадать не должны
        if text and CYR.search(text) and "__" not in text:
            self.found.append(text)


def strings() -> list[str]:
    p = Collect()
    p.feed(open(HTML, encoding="utf-8").read())
    seen, out = set(), []
    for s in p.found:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def main() -> int:
    have = {}
    if os.path.exists(EN):
        have = json.load(open(EN, encoding="utf-8"))
    found = strings()
    missing = [s for s in found if s not in have]

    print(f"строк в разметке: {len(found)}")
    print(f"переведено:       {len(found) - len(missing)}")
    print(f"не хватает:       {len(missing)}")

    if "--write" in sys.argv:
        for s in missing:
            have[s] = ""
        os.makedirs(os.path.dirname(EN), exist_ok=True)
        with open(EN, "w", encoding="utf-8") as f:
            json.dump(have, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print(f"\nдописано в {os.path.relpath(EN, ROOT)}: {len(missing)}")
    elif missing:
        print("\nбез перевода (первые 20):")
        for s in missing[:20]:
            print("  -", s[:90])
        print("\nдописать пустыми:  python reelsi/tools/i18n_extract.py --write")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            cast(Any, _s).reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
