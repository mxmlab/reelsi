# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сверка того, что РЕАЛЬНО собралось в After Effects, с тем, что просил .jsx.

Закрывает вторую половину цикла проверки. `verify_jsx.py` смотрит на .jsx до AE;
этот — на результат ПОСЛЕ. Порядок работы:

  1. в AE открыт собранный проект -> File > Scripts > Run Script File... >
     `tools/ae_inspect.jsx` (кладёт `<проект>.inspect.json` рядом с .aep);
  2. `python tools/verify_ae.py <проект>.inspect.json --jsx Reelsi_out/01_ng10.jsx`

Что сверяется: число слов-субтитров, интро-прекомпов, вставок, кусков рото,
клипов камер, наличие нулов и ПОРЯДОК СЛОЁВ. Порядок — не педантизм: вспышка
перехода уже горела позади персонажа именно из-за него (ARCHITECTURE.md,
2026-07-14), а из дампа это видно сразу.

Чего он НЕ заменяет: кривые, ключи и то, как оно выглядит в движении.
Это по-прежнему только глазами в AE.
"""
import argparse
import json
import os
import re
import sys
from typing import Any, Sequence, cast

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.verify_jsx import Report, extract_structs, WANTED, split_timelines  # noqa: E402

SUBS_COMP = "Субтитры (текст)"
SUBS_COMP_RE = re.compile(r"^Субтитры\s*(\(.*\))?$")
INTRO_COMP_RE = re.compile(r"^текст интро(\s+\d+)?$")

# Порядок слоёв в главном компе, СВЕРХУ ВНИЗ (ARCHITECTURE.md, 2026-07-14):
# дисклеймер -> субтитры -> НУЛЫ -> переходы -> фронт-видео -> рото -> интро/фото-вставки
# -> камеры. Интро и фото-вставки — ОДИН ярус, поэтому в одной группе.
# Нулы — отдельный ярус с 2026-08-11: они собираются в шапку композиции все разом
# (иначе новый нул оставался закопан среди клипов). Индекс слоя в AE: 1 = верхний.
LAYER_ORDER = ["субтитры", "нул", "переходы", "фронт-видео", "рото", "вставки/интро", "камеры"]

# Что обязано лежать ЦЕЛИКОМ выше чего. Проверяем ПАРАМИ, а не непрерывность групп:
# ярусы перемешаны по построению (переход создаётся в цикле вставок и поднимается
# позже). «переходы выше рото» — тот самый баг 2026-07-14: вспышка горела за
# персонажем. Замерено на сборке 10-16.aep: все семь роликов проходят.
ORDER_PAIRS = [("субтитры", "переходы"), ("субтитры", "рото"), ("субтитры", "камеры"),
               ("субтитры", "нул"),
               ("нул", "переходы"), ("нул", "рото"), ("нул", "вставки/интро"),
               ("нул", "камеры"),
               ("переходы", "рото"), ("переходы", "камеры"),
               ("фронт-видео", "рото"),
               ("рото", "вставки/интро"), ("рото", "камеры"), ("вставки/интро", "камеры")]


def _load(path: str) -> Any:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def main_comps(dump: Any, subs_comp: Any = None) -> list[Any]:
    """Главные компы — всё, что не субтитры, не интро и не прекомп вставки.

    Их может быть НЕСКОЛЬКО: `build_combined` («один .jsx на всё») кладёт в один
    проект по компу на файл набора, и тогда «самый слоистый» — не ответ, а
    угадывание. Поэтому список отдаём наверх, а выбор делает вызывающий.
    """
    # `_looks_generated` отсеивает и служебные компы, которые AE создаёт САМ при
    # перетаскивании футажа («roto_<hash>.mp4 Comp 1») — по имени их не отличить,
    # по содержимому видно сразу: наших слоёв в них нет.
    return [c for c in dump.get("comps", [])
            if not SUBS_COMP_RE.match(c.get("name", ""))
            and not INTRO_COMP_RE.match(c.get("name", ""))
            and not str(c.get("name", "")).startswith("INS ")
            and _looks_generated(c.get("layers") or [])]


def pick_main(dump: Any, name: str | None = None) -> tuple[Any, str | None]:
    """-> (комп, ошибка). Один кандидат — берём; несколько — нужен --comp."""
    cands = main_comps(dump)
    if not cands:
        return None, "в дампе нет ни одного композа, похожего на главный"
    if name:
        for c in cands:
            if c.get("name") == name:
                return c, None
        return None, ("компа «%s» в проекте нет. Есть: %s"
                      % (name, ", ".join(str(c.get("name")) for c in cands)))
    if len(cands) > 1:
        return None, ("в проекте %d главных компов (сборка «один .jsx на всё») — "
                      "укажи, какой сверять: --comp %s"
                      % (len(cands), " | --comp ".join(str(c.get("name")) for c in cands)))
    return cands[0], None


def _kind(layer: Any) -> str:
    """Грубая классификация слоя главного компа по имени/источнику (нул — по флагу)."""
    n = layer.get("name") or ""
    src = layer.get("source") or ""
    if n.startswith("Вставка: "):
        # Фото едет прекомпом «INS <файл>» и лежит ПОД рото; видео кладётся футажом
        # и поднимается НАД рото (`moveToBeginning`, флаг insert_video_front).
        # Это разные ярусы, и путать их нельзя — иначе порядок слоёв не сходится.
        return "вставки/интро" if src.startswith("INS ") else "фронт-видео"
    if INTRO_COMP_RE.match(n):
        return "вставки/интро"
    # «Рото маска 2»: AE дописывает « 2» к имени, когда такой слой уже есть
    # (у пяти масок источником стал авто-созданный комп «roto_<hash>.mp4 Comp 1»).
    if n.startswith("Рото маска") or n.startswith("Рото камера"):
        return "рото"
    if n == "Переход":
        return "переходы"
    if n in ("Музыка", "Поп", "Интро SFX", "Whoosh"):
        # Звук. Whoosh идёт в паре с переходом, но это АУДИО — z-порядка у него нет,
        # и раньше он тянул «переходы» вниз стека и портил проверку порядка.
        return "звук"
    if SUBS_COMP_RE.match(n):
        return "субтитры"
    # Нул — по флагу из дампа (`nullLayer`), а не по списку имён: имена нулов растут
    # («вставки кам1 на кам2», «интро на кам2»), и список каждый раз отставал. Список
    # оставлен запасным — старые дампы флага не знают.
    if layer.get("isNull"):
        return "нул"
    if n in ("вставки кам1", "вставки кам2", "вставки кам1 на кам2",
             "интро", "интро на кам2") or n.startswith("Камера "):
        return "нул"
    return "прочее"


def _looks_generated(layers: Any) -> bool:
    """Похож ли комп на сборку из нашего .jsx (а не на ручной проект в AE)."""
    return any((L.get("name") or "").startswith(("Камера ", "Вставка: ", "Субтитры"))
               or (L.get("name") or "") in ("Рото маска", "Рото камера",
                                             "вставки кам1", "вставки кам2", SUBS_COMP)
               for L in layers)


def check(dump: Any, structs: Any, rep: Any, comp_name: str | None = None) -> None:
    main, err = pick_main(dump, comp_name)
    if err:
        rep.err(err)
        return
    layers = main.get("layers") or []
    rep.note("главный комп «%s»: %sx%s @%s, слоёв %d"
             % (main.get("name"), main.get("w"), main.get("h"), main.get("fps"), len(layers)))

    # Сверять имеет смысл ТОЛЬКО проект, собранный нашим .jsx: у него узнаваемые
    # имена слоёв (нулы «Камера N», «Вставка: …», «Рото маска»). Проект, доведённый
    # руками в AE с нуля, устроен иначе — без этой проверки на него сыпался ворох
    # мусорных расхождений, из которого не видно настоящих.
    if not _looks_generated(layers):
        rep.err("комп «%s» не похож на сборку Reelsi: нет ни нулов «Камера N», ни слоёв "
                "«Вставка: …»/«Рото маска». Сверять .jsx не с чем — это ручной проект "
                "или не тот комп." % main.get("name"))
        return

    # Тот ли это .jsx? Если ни один файл камеры из .jsx не встречается среди
    # источников слоёв — сверяют РАЗНЫЕ клипы, и десяток расхождений ниже был бы
    # чистым шумом. Ловим здесь и говорим прямо.
    cams0 = structs.get("CAM") or []
    want_src = {os.path.basename(c.get("path") or "").lower() for c in cams0 if c.get("path")}
    have_src = {(L.get("source") or "").lower() for L in layers if L.get("source")}
    if want_src and have_src and not (want_src & have_src):
        rep.err("это .jsx от другого клипа: в нём камеры %s, а в компе «%s» источники %s"
                % (", ".join(sorted(want_src)), main.get("name"),
                   ", ".join(sorted(list(have_src))[:4])))
        return

    all_comps = dump.get("comps", [])

    # ---- субтитры ----
    # Имя компа субтитров: "Субтитры (<имя ролика>)" или "Субтитры (текст)"
    subs = structs.get("SUBS")
    if isinstance(subs, list):
        target_name = f"Субтитры ({main.get('name')})" if main.get("name") else SUBS_COMP
        scs = [c for c in all_comps if c.get("name") == target_name or SUBS_COMP_RE.match(c.get("name", ""))]
        counts = [len(c.get("layers") or []) for c in scs]
        if subs and not scs:
            rep.err("в .jsx %d слов-субтитров, а компа субтитров в проекте нет" % len(subs))
        elif len(scs) == 1:
            if counts[0] != len(subs):
                rep.err("слов-субтитров: в .jsx %d, в AE %d" % (len(subs), counts[0]))
            else:
                rep.note("субтитры: %d слов — сходится" % counts[0])
        elif scs:
            if len(subs) in counts:
                rep.note("субтитры: %d слов — сходится (сборный проект, компов субтитров %d)"
                         % (len(subs), len(scs)))
            else:
                rep.err("слов-субтитров в .jsx %d, а в компах субтитров проекта %s"
                        % (len(subs), counts))

    # ---- интро ----
    # Считаем СЛОИ главного компа, а не компы проекта: имена интро-прекомпов
    # («текст интро 1») повторяются у каждого файла набора, а слой лежит в своём.
    intro = structs.get("INTRO_GROUPS")
    if isinstance(intro, list):
        got = sum(1 for L in layers if INTRO_COMP_RE.match(L.get("name") or ""))
        if got != len(intro):
            rep.err("интро-прекомпов: в .jsx %d, слоёв интро в компе «%s» %d"
                    % (len(intro), main.get("name"), got))
        elif intro:
            rep.note("интро: %d прекомп(ов) — сходится" % got)

    # ---- вставки ----
    ins = structs.get("INSERTS")
    if isinstance(ins, list):
        # По ИМЕНИ слоя, а не по ярусу: ярус «вставки/интро» общий с интро-прекомпами.
        got = sum(1 for L in layers if (L.get("name") or "").startswith("Вставка: "))
        if got != len(ins):
            # Классика: .webp/.avif — .jsx собрался, слоя нет (2026-07-22).
            rep.err("вставок: в .jsx %d, в AE %d%s"
                    % (len(ins), got,
                       " — проверь форматы файлов, AE не читает webp/avif" if got < len(ins) else ""))
        elif ins:
            rep.note("вставки: %d — сходится" % got)

    # ---- рото ----
    roto = structs.get("ROTO")
    if isinstance(roto, list):
        pairs = sum(1 for L in layers if (L.get("name") or "").startswith("Рото маска"))
        if pairs != len(roto):
            rep.err("кусков рото: в .jsx %d, масок в AE %d" % (len(roto), pairs))
        elif roto:
            rep.note("рото: %d кусков — сходится" % pairs)

    # ---- камеры и нулы ----
    cams = structs.get("CAM")
    if isinstance(cams, list):
        for ci, c in enumerate(cams):
            label = "Камера %d" % (ci + 1)
            if not any(L.get("name") == label for L in layers):
                rep.err("нет нула «%s» — вставки и интро останутся без родителя" % label)
        # Клипы камер считаем СУММОЙ, а не по камерам: у камер бывают файлы с
        # ОДИНАКОВЫМ именем (`камера1\ng10.mov` и `Камера2\ng10.mov`), и по source
        # их не различить. Слои рото исключаем — это копии той же камеры.
        want = 0
        for ci, c in enumerate(cams):
            want += sum(1 for cl in (c.get("clips") or [])
                        if ci == 0 or (len(cl) > 4 and cl[4]))   # скрытые клипы кам2 не создаются
        srcs = {os.path.basename(c.get("path") or "") for c in cams if c.get("path")}
        got = sum(1 for L in layers
                  if (L.get("source") or "") in srcs and _kind(L) != "рото")
        if want != got:
            rep.err("клипов камер: в .jsx %d, в AE %d (файлы %s)"
                    % (want, got, ", ".join(sorted(srcs))))
        else:
            rep.note("клипы камер: %d — сходится" % got)
        for nul in ("вставки кам1", "интро"):
            if not any(L.get("name") == nul for L in layers):
                rep.warn("нет нула «%s»" % nul)

    # ---- порядок слоёв ----
    # НЕ проверяем непрерывность ярусов: переходы ставятся у своего ката и законно
    # чередуются со вставками (замер на реальных проектах — чередование есть везде).
    # Проверяем ПАРНЫЕ инварианты: что обязано лежать целиком выше чего.
    pos: dict[str, list[int]]
    named: dict[tuple[str, int], str]
    pos, named = {}, {}
    for L in layers:
        k = _kind(L)
        if k in LAYER_ORDER:
            i = L.get("index", 0)
            pos.setdefault(k, []).append(i)
            named[(k, i)] = L.get("name") or "?"
    # ПРЕДУПРЕЖДЕНИЕ, а не ошибка: канонический порядок описан в ARCHITECTURE.md, но
    # проект после сборки правят руками, и переставленный слой — часто осознанный выбор.
    # Проверка, которая валит сборку по неподтверждённому поводу, быстро учит её игнорировать.
    for hi, lo in ORDER_PAIRS:
        if hi in pos and lo in pos and max(pos[hi]) > min(pos[lo]):
            ihi, ilo = max(pos[hi]), min(pos[lo])
            rep.warn("порядок слоёв: «%s» ожидается выше «%s» (ARCHITECTURE.md), а «%s» "
                     "(слой %d) лежит под «%s» (слой %d) — проверь, если это не ручная правка"
                     % (hi, lo, named.get((hi, ihi), hi), ihi, named.get((lo, ilo), lo), ilo))
    if pos:
        rep.note("ярусы сверху вниз: " + " -> ".join(
            k for k in LAYER_ORDER if k in pos))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сверка проекта AE с собранным .jsx")
    ap.add_argument("inspect", help="<проект>.inspect.json от ae_inspect.jsx")
    ap.add_argument("--jsx", required=True, help=".jsx, которым собирался проект")
    ap.add_argument("--comp", help="какой главный комп сверять (для сборки «один .jsx на всё»)")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)

    try:
        cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                       # noqa: BLE001
        pass

    rep = Report(a.inspect)
    if not os.path.exists(a.inspect):
        print("[ERR] нет дампа: %s\n      Запусти в AE: File > Scripts > Run Script File... > tools/ae_inspect.jsx"
              % a.inspect)
        return 1
    if not os.path.exists(a.jsx):
        print("[ERR] нет .jsx: %s" % a.jsx)
        return 1

    dump = _load(a.inspect)
    raw = open(a.jsx, encoding="utf-8-sig", errors="replace").read()
    blocks = split_timelines(raw)
    idx = 0
    if a.comp:
        cands = main_comps(dump)
        for i, c in enumerate(cands):
            if c.get("name") == a.comp:
                idx = i
                break
    idx = min(idx, len(blocks) - 1)
    structs = extract_structs(blocks[idx])
    for name in WANTED:
        v = structs.get(name)
        if isinstance(v, tuple) and v and v[0] == "__BROKEN__":
            structs.pop(name)

    check(dump, structs, rep, comp_name=a.comp)

    print("%s %s  vs  %s" % ("[OK ]" if rep.ok else "[ERR]",
                             os.path.basename(a.inspect), os.path.basename(a.jsx)))
    for m in rep.errors:
        print("   [ERR]  " + m)
    for m in rep.warns:
        print("   [WARN] " + m)
    if not a.quiet:
        for m in rep.info:
            print("   .      " + m)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
