# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Камера плана сцены (этап 6 распила scene_plan).

`scene_plan` был одной функцией на 3253 строки с 24 вложенными функциями, делившими
состояние замыканиями. Этапы 1–5 вынесли блок субтитров, расчёт
интро, вставки, звук и подстановки шаблона интро. Последний этап выносит сюда ВСЮ камеру:

* параметры Камеры 1 из стиля — точка наезда (`cam1_zoom_cx/cy`), сдвиг кадра (pan) и
  поворот: при дефолтах 0.5/0.5, 0/0 и 0 подстановки шаблона пусты и .jsx не меняется
  ни на байт (golden);
* ключи зума по режимам (`cam1_zoom`: pulse, jump, drift, none), наезды в тейках
  (`cam1_take_*`, в том числе по жёлтым словам) и «заполнение кадра» (`cam1_fit`) —
  оно умножается на ключи РОВНО ОДИН РАЗ, поэтому слежение за головой и автофит интро
  берут ключи уже с fit;
* тип интерполяции ключей (`holds`) и готовые подстановки шаблона CAM1_SCALE, CAM1_HOLDS,
  CAM1_EASE;
* разметку рото (`roto_plan`) и данные `plan["roto"]` — маски по ней делает `to_ae_full`
  на GPU, предпросмотр опирается на те же фрагменты;
* слежение за головой (`cam1_head_*`): чтение кэша `<стем>.head.json`, ключи CAM1_FOLLOW
  и их темпоральный ease;
* данные плана для предпросмотра — `plan["zoom"]` целиком (holds/fit/cx/cy/pan/rot/keys/
  ease/follow) и подстановки якоря точки наезда, позиций и поворотов рото-копий.

Перенос ПОСТРОЧНЫЙ: поведение, числа, порядок операций и ТЕКСТ подстановок .jsx не менялись
ни на байт (проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением
.jsx/плана). Имена локальных переменных оставлены как в scene_plan — поэтому тело перенесено
дословно, а входы распаковываются в преамбуле.

Почему дверь одна. Части камеры стояли в трёх местах scene_plan (параметры стиля — рядом с
макетом спикера, ключи зума — сразу после субтитров, рото — после данных вставок, слежение —
после затемнения интро), но зависимого кода между ними нет: всё, что камере нужно (слова
субтитров и индексы жёлтых — для наездов в тейках, флаг `roto` — для разметки, путь XML —
для кэша головы), готово к первой же строке блока. Порядок операций ВНУТРИ камеры сохранён
как был: ключи зума -> fit -> holds -> рото -> слежение -> данные плана -> подстановки.
Вторая дверь завела бы вторую копию ключей зума: их читают и подстановки шаблона, и
`plan["zoom"]` предпросмотра, и автофит интро (plan_intro.py).

Общее с другими блоками остаётся в `build.py` и приходит параметрами; стиль приходит
структурой `StyleValues` одним полем `style` (её читает один раз
`plan_style.read_style`, своей копии чтения ключей в модуле нет). Таблицы камеры живут
в `layout.py` для всех блоков сразу:
`_cam1_zoom_keys`/`_cam1_jump_keys`/`_cam1_drift_keys`, `_zoom_key_holds`/`_zoom_key_eases`,
`_span_roto_plan`, `_cam1_follow_keys`. `_active_cam_at` камера не зовёт — он общий у
вставок и интро и остаётся в build.py. `to_ae_full` (трекинг головы ПЕРЕД сборкой) не
тронут: здесь только чтение уже готового кэша.
"""
from dataclasses import dataclass, field
from typing import Any, Sequence

from .jsutil import _jd, _r
from .layout import (EASE_DEFAULT, HL_ZOOM_MAX_PER_PIECE, HL_ZOOM_MIN_PCT,
                     HL_ZOOM_SECOND_MIN_S, _cam1_drift_keys, _cam1_follow_keys,
                     _cam1_jump_keys, _cam1_zoom_keys, _hl_phrase_groups,
                     _merge_same_frame_keys, _phrase_strength, _span_roto_plan,
                     _zoom_key_eases, _zoom_key_holds)
from .plan_style import StyleValues


@dataclass(frozen=True)
class CameraInputs:
    """Вход камеры: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan. `cam1_scale` — kwarg сборки: None
    означает «посчитать по режиму стиля», готовый список ключей приходит из тестов и
    предпросмотра и режим перебивает. `subs`/`hl` нужны только ветке «наезды по жёлтым»
    (`cam1_take_yellow`): по временам жёлтых слов ставятся ключи. `style` — структура
    стиля, прочитанная один раз: из неё параметры точки наезда, pan и
    поворота, режим зума и его числа, fit, слежение за головой.
    """
    # Камеры из разбора XML: по их клипам считаются и ключи зума, и разметка рото.
    cams: list[dict[str, Any]]
    # Ролик из разбора XML: ширина/высота/длительность в кадрах и fps без запаса.
    meta: dict[str, Any]
    # Частота кадров как _fps0 (meta["fps"] or 60) — ею считаются ключи и участки головы.
    fps: float
    # Слова субтитров (уже без вырезанных слов интро) и индексы жёлтых — для наездов
    # в тейках по жёлтым. Выделенные цветом слова интро приходят отдельно (intro_groups/
    # intro_remove/subs_all): их вынул plan_words, и в `subs` их уже нет.
    subs: list[Any]
    hl: set[Any]
    # Резолвнутый и прочитанный стиль (plan_style.read_style).
    style: StyleValues
    # Явные ключи зума (kwarg scene_plan) или None — тогда режим из стиля.
    cam1_scale: list[Any] | None
    # Галка «Авто-ротоскоп»: выключена — разметка пустая, `_roto_js` не позовёт GPU.
    roto: bool
    # Путь XML: рядом с ним лежит кэш трека головы `<стем>.head.json`.
    xml_path: str
    # Рамка кадра камеры 1 (поле `frame` профиля спикера + посчитанные сдвиг dx/dy и
    # масштаб zoom — см. `core/frame.py`), None — камера без рамки. Нужна слежению за
    # головой: зажим сдвига считает края СМЕЩЁННОГО и увеличенного слоя, иначе при
    # рамке слежение уводит край кадра внутрь.
    cam1_frame: dict[str, Any] | None = None
    # Строки интро (сырые, из сборки): слова интро, выделенные цветом (color yellow/accent),
    # наезжают наравне с жёлтыми словами ролика. Пусто — таких слов нет, поведение прежнее.
    intro_groups: list[list[dict[str, Any]]] = field(default_factory=list)
    # Индексы слов интро в ИСХОДНОМ списке слов (`intro_remove` сборки — тот же список,
    # по которому plan_words вынимает слова интро из титров) и сам исходный список
    # (`censor_source`). Индексы нужны, чтобы взять у слова не только начало, но и конец:
    # строки интро несут одно время (начало), а фразе нужен её конец.
    intro_remove: list[int] = field(default_factory=list)
    subs_all: list[Any] = field(default_factory=list)
    # Сила жёлтых слов (core/emphasis.EmphasisView): индекс слова -> 0..1 плюс признак
    # «посчитано». Нет (None) — сайдкар не читался/не посчитан: наезд на КАЖДУЮ фразу,
    # как раньше. Галку «наезд только на сильные жёлтые» камера берёт из стиля.
    emph: Any = None


@dataclass(frozen=True)
class CameraPlan:
    """Выход камеры: ровно те имена, что `scene_plan` читает дальше.

    Ключи зума отдаются уже с «заполнением кадра»: их читают подстановки шаблона
    (`cam1scale_js`), `plan["zoom"]`, автофит интро (plan_intro.py) и слежение. `roto` —
    готовый `plan["roto"]` (данные масок), `zoom` — готовый `plan["zoom"]` (предпросмотр).
    `*_js`/`*_decl` — подстановки шаблона: при выключенных ручках они пустые или прежние,
    и .jsx остаётся байт в байт (golden).
    """
    cam1_scale: list[Any]   # [[кадр, %], ...] (+ режим drift третьим элементом), уже с fit
    holds: list[Any]        # тип интерполяции каждого ключа: 1=HOLD, 0=BEZIER
    cam1scale_js: str       # подстановка CAM1_SCALE
    cam1_ease_js: str       # подстановка CAM1_EASE: [in, out] влияния на каждый ключ
    cam1holds_js: str       # подстановка CAM1_HOLDS (то же, что zoom["holds"])
    roto: list[dict[str, Any]]  # plan["roto"]: фрагменты масок (ci/ts/te/src/scale)
    zoom: dict[str, Any]    # plan["zoom"]: holds/fit/cx/cy/pan/rot/keys/ease (+follow)
    cam1_cx: float          # точка наезда Камеры 1, доли кадра (подстановка cam1_cx)
    cam1_cy: float
    cam1_moved: bool        # камера сдвинута/повёрнута/следит: иначе подстановки пустые
    cam1_follow_decl: str   # объявление var CAM1_FOLLOW
    cam1_follow_js: str     # код слежения: ключи на X-координату нула Камеры 1
    cam1_anchor: str        # якорь и позиция нула от точки наезда
    cam2_js: str            # якорь и ключи зума нула Камеры 2 (при галке «Зум и на камере 2»)
    roto_pos_cc: str        # компенсация позиции рото-копии после привязки к нулу
    roto_pos_mk: str        # компенсация позиции маски после привязки к нулу
    cam1_rot_decl: str      # var CAM1_ROT
    cam1_rot_cam: str       # поворот слоя Камеры 1
    cam2_rot_decl: str      # var CAM2_ROT
    cam2_rot_cam: str       # поворот слоя Камеры 2
    roto_rot_cc: str        # поворот рото-копии Камеры 1
    roto_rot_mk: str        # поворот маски Камеры 1
    cam2_active: bool = False  # Камера 2 активна (трансформ/зум)
    # Ключи зума Камеры 2 и тип интерполяции каждого: автофит интро, попавшего на
    # перебивку, считает по НИМ, а не по ключам Камеры 1 (свой зум, свой fit).
    # Пусто — Камера 2 неактивна: автофит такой группы видит зум 100%.
    cam2_scale: list[Any] = field(default_factory=list)
    cam2_holds: list[Any] = field(default_factory=list)
    cam2_follow_decl: str = ""
    cam2_follow_js: str = ""


def _intro_hl_words(intro_groups: list[list[dict[str, Any]]], intro_remove: list[int],
                    subs_all: list[Any]) -> list[tuple[float, float]]:
    """Слова интро, выделенные цветом, — (начало, конец) в кадрах.

    Индексы берутся из `intro_remove` — того же списка индексов, по которому
    `plan_words` вынимает слова интро из титров, — нарезанного по строкам интро в том
    порядке, в котором их заполняет `resolveIntroFor` (static/app/90-ae.js): каждая
    строка забирает столько индексов, сколько в ней слов. Времена — из `subs_all`
    (слова ДО вырезания интро): строка интро несёт только начало слова, а фразе нужен
    и его конец. Второго сопоставления «строка -> слова subs» здесь не заводится.
    """
    out: list[tuple[float, float]] = []
    off = 0
    for grp in intro_groups:
        for x in grp:
            n = len(x.get("words") or (x.get("text") or "").split())
            idxs = intro_remove[off:off + n]
            off += n
            if x.get("color") not in ("yellow", "accent"):
                continue
            for k in idxs:
                if 0 <= int(k) < len(subs_all):
                    s, e, _w = subs_all[int(k)]
                    out.append((float(s), float(e)))
    return out


def _yellow_phrases(subs: list[Any], hl: set[Any], intro_words: list[tuple[float, float]],
                    fps: float) -> list[tuple[float, float]]:
    """Фразы хайлайта для наездов: жёлтые слова ролика (индексы `hl`) плюс выделенные
    цветом слова интро.

    Слова интро лежат ВНЕ `subs` (их вынул `plan_words`), поэтому у них свой индексный
    ряд: с соседними по индексу словами ролика они не сливаются, а внутри ряда фраза
    собирается тем же правилом `_hl_phrases`.
    """
    return [ph for ph, _members in _yellow_phrase_groups(subs, hl, intro_words, fps)]


def _yellow_phrase_groups(subs: list[Any], hl: set[Any], intro_words: list[tuple[float, float]],
                          fps: float) -> list[tuple[tuple[float, float], list[int]]]:
    """То же, что `_yellow_phrases`, но с составом фразы: [(фраза, [индексы слов])].

    Состав нужен правилу «наезд только на сильные жёлтые»: сила фразы — максимум сил её
    слов, а пара (t_start, t_end) о составе молчит. Группировка ровно та же, что у
    `_yellow_phrases` — обе зовут один `_hl_phrases`/`_hl_phrase_groups` из layout.
    """
    items: list[tuple[int, float, float]] = [(int(k), subs[k][0], subs[k][1]) for k in hl]
    items += [(len(subs) + j, s, e) for j, (s, e) in enumerate(intro_words)]
    return _hl_phrase_groups(items, fps)


def _yellow_need(subs: list[Any], hl: set[Any],
                 intro_words: list[tuple[float, float]]) -> list[int]:
    """Индексы слов, по которым ставятся наезды хайлайта: жёлтые ролика и слова интро.

    Слова интро живут вне `subs`, их индекс продолжает ряд (`len(subs) + j`) — ровно так
    их нумерует расчёт силы (`core/emphasis.py`, сайдкар), поэтому силы и находятся.
    """
    return [int(k) for k in (hl or ())] + \
           [len(subs) + j for j in range(len(intro_words or ()))]


def _zoom_keys_js(null_name: str, ease_literal: str, ease_default: float,
                   scale_name: str = "CAM1_SCALE", holds_name: str = "CAM1_HOLDS",
                   ease_name: str = "CAM1_EASE") -> str:
    """Ключи зума на нул `null_name`: посимвольно ТОТ ЖЕ блок, что стоит у Камеры 1.

    Одна дверь на обе камеры: у Камеры 2 свои массивы (CAM2_SCALE/CAM2_HOLDS/CAM2_EASE),
    но логика наложения ключей в AE полностью идентична.
    """
    return (
        "\n    if (%(nul)s && %(scale)s.length){\n"
        "        var sc = %(nul)s.property(\"ADBE Transform Group\").property(\"ADBE Scale\");\n"
        "        var %(ease_name)s=%(ease)s;  // [[in,out], ...] влияние ease на КАЖДЫЙ ключ — посчитано в Python\n"
        "        // 1) setValueAtTime всех ключей\n"
        "        for (var z=0; z<%(scale)s.length; z++)\n"
        "            sc.setValueAtTime(%(scale)s[z][0]/FPS, [%(scale)s[z][1], %(scale)s[z][1]]);\n"
        "        // 2) всем ключам BEZIER/BEZIER\n"
        "        for (var kb=1; kb<=sc.numKeys; kb++)\n"
        "            sc.setInterpolationTypeAtKey(kb, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);\n"
        "        // 3) temporalEase(sc, eIns, eOuts) на все ключи\n"
        "        var eIns=[], eOuts=[];\n"
        "        for (var z2=0; z2<sc.numKeys; z2++){\n"
        "            var ee=%(ease_name)s[z2]||[%(ease_default)g,%(ease_default)g];\n"
        "            eIns.push(ee[0]); eOuts.push(ee[1]);\n"
        "        }\n"
        "        // Scale 2D-нула: value.length=2, а AE ждёт 1 — откат внутри, ошибка не прячется\n"
        "        temporalEase(sc, eIns, eOuts);\n"
        "        // 4) затем для каждого ключа k (1-based): in / out HOLD или BEZIER по %(holds)s\n"
        "        for (var k=1; k<=sc.numKeys; k++){\n"
        "            var inHold = (k > 1 && %(holds)s[k - 2]) ? KeyframeInterpolationType.HOLD : KeyframeInterpolationType.BEZIER;\n"
        "            var outHold = (%(holds)s[k - 1]) ? KeyframeInterpolationType.HOLD : KeyframeInterpolationType.BEZIER;\n"
        "            if (inHold === KeyframeInterpolationType.HOLD || outHold === KeyframeInterpolationType.HOLD)\n"
        "                sc.setInterpolationTypeAtKey(k, inHold, outHold);\n"
        "        }\n"
        "        // 5) ключей в AE может оказаться МЕНЬШЕ, чем в плане: setValueAtTime на\n"
        "        //    занятый момент перезаписывает ключ, и тогда CAM*_HOLDS/CAM*_EASE\n"
        "        //    съезжают по индексам — ease/HOLD уезжают на чужой ключ. Склейку\n"
        "        //    одинаковых моментов делает Python (_merge_same_frame_keys), так что\n"
        "        //    расхождение = дефект плана: пишем в лог, сборку не роняем.\n"
        "        if (sc.numKeys !== %(scale)s.length)\n"
        "            _LOG(\"ключи зума: в AE \" + sc.numKeys + \", в плане \" + %(scale)s.length + \" — ease/HOLD могут сдвинуться\");\n"
        "    }"
    ) % {"nul": null_name, "ease": ease_literal, "ease_default": ease_default,
         "scale": scale_name, "holds": holds_name, "ease_name": ease_name}


def _yellow_scores_map(emph: Any, need: Sequence[int]) -> dict[int, float] | None:
    """Силы жёлтых слов клипа из сайдкара или None, если покрыты не все нужные слова.

    `need` — индексы слов, по которым ставятся наезды: жёлтые слова ролика и слова
    интро (их индексный ряд продолжает слова ролика — как в `_yellow_phrases`).
    Способ оценки (`hl_zoom_strength`) выбран ещё при чтении сайдкара
    (`emphasis.read_emphasis(..., mode=...)`), поэтому `emph.scores` — уже числа нужной
    шкалы; своего выбора здесь нет, иначе план и превью могли бы разойтись.
    Пропуск (жёлтое добавили после расчёта) — None: правило вернётся к прежнему
    поведению, а не сочтёт слово слабым молча. Компонента выбранного способа, которой
    в сайдкаре нет (в режиме «по голосу» эмоций не считали), — тот же пропуск.
    """
    if emph is None or not getattr(emph, "valid", False):
        return None
    scores: dict[int, float] = dict(getattr(emph, "scores", {}) or {})
    want = {int(i) for i in need}
    if not want or any(i not in scores for i in want):
        return None
    return scores


def _yellow_strong_on(style: Any, prefix: str) -> bool:
    """Галка «наезд только на сильные жёлтые» из структуры стиля (или сырого словаря).

    Заглушка осталась только для сырого словаря (тесты и вызовы мимо `read_style`);
    боевой путь читает ключ в `plan_style.read_style` — одна дверь чтения ключей.
    """
    if isinstance(style, dict):
        v = style.get(f"{prefix}_yellow_zoom_strong")
    else:
        v = getattr(style, f"{prefix}_yellow_zoom_strong", None)
    return True if v is None else bool(v)


def _yellow_knob(style: Any, key: str, default: Any) -> Any:
    """Ручка силы жёлтых из структуры стиля (или сырого словаря) с дефолтом.

    Ручки общие для обеих камер (`hl_zoom_*`): способ оценки, порог силы, предел наездов
    на кусок и порог длины для второго наезда. Сырой словарь — тот же путь для тестов и
    вызовов мимо `read_style`.
    """
    v = style.get(key) if isinstance(style, dict) else getattr(style, key, None)
    return default if v is None else v


def _take_yellow(take: dict[str, Any], groups: Any, scores: Any, strong: bool,
                 clip_scores: Any = None, style: Any = None) -> None:
    """Дописать в `take` правило силы жёлтых: силы фраз и ручки правила.

    `groups` — фразы хайлайта с составом (`_yellow_phrase_groups`: та же группировка,
    что у `take["words"]`); `scores` — силы слов клипа из сайдкара
    (`core/emphasis.EmphasisView.scores`, индекс слова -> 0..1). Сила фразы — максимум
    сил её слов (наезд приходит на фразу целиком), `clip_scores` — силы ВСЕХ жёлтых
    клипа: по ним правило считает порог силы (ручка «Порог силы, %»).

    Ручки правила (`style`: `hl_zoom_min_pct`, `hl_zoom_max_per_piece`,
    `hl_zoom_second_min_s`) уезжают в `take` — их читает `_take_zoom_segment_keys`.
    Ключа стиля нет — берётся дефолт правила (бывшие константы `HL_ZOOM_*`; у порога силы
    это p70 жёлтых клипа, замер 02.10.2026).

    Сил нет или они покрывают не все жёлтые слова (жёлтое добавили руками после
    расчёта) — в `take` уезжает только галка, и наезд идёт на каждую фразу, как раньше.
    """
    if scores:
        need = [int(i) for _ph, members in groups for i in members]
        if need and all(i in scores for i in need):
            strengths = [_phrase_strength(members, scores) for _ph, members in groups]
            take["yellow_strengths"] = [s if s is not None else 0.0 for s in strengths]
            # Силы ВСЕХ жёлтых клипа (не только звучащих в куске): порог силы — свойство
            # ролика, и кусок, где лучшее слово слабее порога, не наезжает вовсе.
            take["yellow_clip_scores"] = [float(v) for v in (clip_scores or {}).values()]
    take["yellow_strong"] = bool(strong)
    if style is not None:
        # Дефолты ручек — константы правила из layout (в модуль уже импортированы).
        take["yellow_min_pct"] = float(_yellow_knob(style, "hl_zoom_min_pct", HL_ZOOM_MIN_PCT))
        take["yellow_max_per_piece"] = int(_yellow_knob(style, "hl_zoom_max_per_piece",
                                                       HL_ZOOM_MAX_PER_PIECE))
        take["yellow_second_min_s"] = float(_yellow_knob(style, "hl_zoom_second_min_s",
                                                        HL_ZOOM_SECOND_MIN_S))


def _anchor_js(nul: str, cx: float, cy: float, w: float, h: float,
               pan_x: float, pan_y: float) -> str:
    """Якорь и позиция нула от точки наезда: неподвижна именно точка, а не центр кадра.

    У Камеры 1 и Камеры 2 свои точка наезда и сдвиг кадра (pan_x/y),
    подстановка печатает координаты якоря и позиции нула камеры.
    """
    return ("\n    // точка наезда камеры: anchor+position от неё, "
            "неподвижна именно она\n"
            "    if(%(nul)s){ %(nul)s.property(\"ADBE Transform Group\").property(\"ADBE Anchor Point\").setValue([%(ax)g,%(ay)g]);"
            " %(nul)s.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([%(px)g,%(py)g]); }"
            % {"nul": nul, "ax": cx * w - w / 2, "ay": cy * h - h / 2,
               "px": cx * w + pan_x, "py": cy * h + pan_y})


def _follow_keys_js(null_name: str, follow_name: str, cam_idx: int) -> str:
    """Ключи слежения за головой по X на нул камеры пачкой `setValuesAtTimes`.

    Ставятся одним вызовом `setValuesAtTimes(times, values)` — в AE это на два порядка
    быстрее поштучного `setValueAtTime`. По умолчанию AE даёт новым ключам линейную
    интерполяцию. Если у пользователя в настройках AE дефолт другой — запасной цикл
    ставит LINEAR.
    """
    return (
        f"\n    // слежение за головой по X: ключи на X-координату нула Камеры {cam_idx}\n"
        f"    if ({null_name} && typeof {follow_name} !== \"undefined\" && {follow_name}.length){{\n"
        f"        var pos = {null_name}.property(\"ADBE Transform Group\").property(\"ADBE Position\");\n"
        f"        pos.dimensionsSeparated = true;\n"
        f"        var posX = {null_name}.property(\"ADBE Transform Group\").property(\"ADBE Position_0\");\n"
        f"        var base = posX.value;\n"
        f"        var times = [], values = [];\n"
        f"        for (var fi = 0; fi < {follow_name}.length; fi++){{\n"
        f"            times.push({follow_name}[fi][0] / FPS);\n"
        f"            values.push(base + {follow_name}[fi][1]);\n"
        f"        }}\n"
        f"        posX.setValuesAtTimes(times, values);\n"
        f"        if (posX.numKeys > 0 && posX.keyInInterpolationType(1) !== KeyframeInterpolationType.LINEAR){{\n"
        f"            for (var ki = 1; ki <= posX.numKeys; ki++)\n"
        f"                posX.setInterpolationTypeAtKey(ki, KeyframeInterpolationType.LINEAR, KeyframeInterpolationType.LINEAR);\n"
        f"        }}\n"
        f"    }}\n"
    )


def plan_camera(inp: CameraInputs) -> CameraPlan:
    """Камера плана сцены: параметры из стиля, ключи зума, рото, слежение, подстановки.

    Тело — дословный перенос блоков из scene_plan (до распила — строки 657-664, 803-845,
    861-868, 1252-1288, 1467-1486 и 1559-1582): имена локальных переменных оставлены
    прежними, поэтому ни одна строка не переписана. Порядок операций тот же, и он важен:
    «заполнение кадра» умножается на ключи ДО holds и ДО слежения — иначе слежение
    сравнивало бы порог с ключами без fit, а `plan["zoom"]` разошёлся бы с .jsx.
    """
    cams = inp.cams
    meta = inp.meta
    _fps0 = inp.fps
    subs, hl = inp.subs, inp.hl
    # Стиль — структурой, прочитанной один раз: обёрток _sv/_sv_or у камеры
    # больше нет, значения уже приведены к своим типам.
    style = inp.style
    cam1_scale = inp.cam1_scale
    xml_path = inp.xml_path
    # Флаг ротоскопа: в scene_plan он звался `roto`, но здесь именем `roto` называется и
    # готовый список плана — разводим их, чтобы не читать флаг после его использования.
    _roto_on = inp.roto

    # Макет спикера: точка наезда камеры, сдвиг кадра (pan) и поворот. Дефолты
    # 0.5/0.5, 0/0 и 0 — ровно то, что AE ставит сам: подстановки шаблона при них пустые,
    # .jsx не меняется ни на байт (golden).
    cam1_cx = style.cam1_zoom_cx
    cam1_cy = style.cam1_zoom_cy
    pan_x = style.cam1_pan_x
    pan_y = style.cam1_pan_y
    rot = style.cam1_rot
    # Выделенные цветом слова интро: их фразы идут в тот же список наездов, что и жёлтые
    # слова ролика. Индексы и времена берёт одна дверь — _intro_hl_words (второй копии
    # сопоставления «строка интро -> слово subs» нет).
    _intro_hl = _intro_hl_words(inp.intro_groups, inp.intro_remove, inp.subs_all)
    _c1zoom = style.cam1_zoom         # pulse = наезд с откатом | jump = резкие скачки | drift = скачок+плавный дрейф 100–160% | none = нет зума
    if cam1_scale is None:                             # авто-зум по сменам кам1→кам2
        _zstart = style.cam1_zoom_start
        _zbig = style.cam1_zoom_big
        _zlo = style.cam1_zoom_lo
        _zhi = style.cam1_zoom_hi
        _ztake: dict[str, Any] | None = None
        if style.cam1_take_zoom or style.cam1_yellow_zoom:
            _ztake = {
                "min_s": style.cam1_take_min,
                "lo": style.cam1_take_lo,
                "hi": style.cam1_take_hi,
                "hold_s": style.cam1_take_hold,
                "out_s": style.cam1_take_out,
                "long_on": bool(style.cam1_take_zoom),
                "yellow_on": bool(style.cam1_yellow_zoom),
            }
            if style.cam1_yellow_zoom:
                _yellow_groups = _yellow_phrase_groups(subs, hl, _intro_hl, _fps0)
                _ztake["words"] = [ph for ph, _m in _yellow_groups]
                _take_yellow(_ztake, _yellow_groups,
                             _yellow_scores_map(inp.emph, _yellow_need(subs, hl, _intro_hl)),
                             _yellow_strong_on(style, "cam1"),
                             clip_scores=getattr(inp.emph, "scores", None), style=style)
        if _c1zoom == "none":
            if _zstart or _ztake:
                cam1_scale = _cam1_jump_keys(cams, lo=100.0, hi=100.0, fps=meta["fps"], start=_zstart, big=_zbig, take=_ztake)
            else:
                cam1_scale = [(0, 100.0)]
        elif _c1zoom == "drift":
            _zdlo = style.cam1_drift_lo
            _zdhi = style.cam1_drift_hi
            cam1_scale = _cam1_drift_keys(cams, lo=_zdlo, hi=_zdhi, fps=meta["fps"], big=_zbig, start=_zstart, take=_ztake)
        elif _c1zoom == "jump":
            cam1_scale = _cam1_jump_keys(cams, lo=_zlo, hi=_zhi, fps=meta["fps"], start=_zstart, big=_zbig, take=_ztake)
        else:
            cam1_scale = _cam1_zoom_keys(cams, big=_zbig, lo=_zlo, hi=_zhi, fps=meta["fps"], start=_zstart, take=_ztake)
        # Склейка ключей на ОДНОМ кадре — сразу на выходе генератора и ДО fit, holds,
        # подстановок и слежения: скачок на срезе и подъезд хайлайта могут встать на
        # один кадр, а AE `setValueAtTime` перезапишет ключ — тогда `holds`/`ease` по
        # индексам съедут на один (наезд станет HOLD, удержание плавным). План, .jsx,
        # превью (`plan.zoom.keys`) и слежение обязаны видеть один и тот же список.
        cam1_scale = _merge_same_frame_keys(cam1_scale)
    # «Заполнение кадра» — общий множитель зума Камеры 1, и умножается он РОВНО
    # ЗДЕСЬ, один раз. Раньше fit сидел в Scale слоёв клипа и рото, и кадр рос вокруг своего
    # центра, а вставки кам1 с интро не росли вовсе — в превью кадр хороший, в AE уезжает на
    # 240–335 px (ipvZoomAt множит fit на ключи и масштабирует ВСЁ вокруг точки наезда).
    # Дальше ключи уже с fit берут все: CAM1_SCALE, автофит интро (_zoom_max), слежение
    # (_cam1_follow_keys) и план. Второй копии умножения не заводить.
    _fit_k = style.cam1_fit / 100.0
    if _fit_k != 1.0:
        cam1_scale = [(f, round(v * _fit_k, 2), *rest) for f, v, *rest in (cam1_scale or [])]
    holds = _zoom_key_holds(cam1_scale or [], legacy_hold=(_c1zoom == "jump"))
    # 3-й элемент (mode: 1=HOLD, 0=BEZIER) эмитим только если он есть (drift); 2-элементные — легаси
    cam1scale_js = _jd([([_r(f), _r(v)] + ([int(rest[0])] if rest else []))
                        for f, v, *rest in (cam1_scale or [])])
    # ease на каждый ключ зума: JS больше не смотрит соседей/режимы, а берёт готовые
    # [in, out] влияния из данных
    cam1_ease_js = _jd(_zoom_key_eases(cam1_scale or []))
    cam1holds_js = _jd([1 if h else 0 for h in holds])
    # ---- Камера 2: независимый зум, свой трансформ (fit/pan/rot), своя точка наезда ----
    _c2zoom = style.cam2_zoom
    cam2_cx = style.cam2_zoom_cx
    cam2_cy = style.cam2_zoom_cy
    cam2_fit = style.cam2_fit
    cam2_pan_x = style.cam2_pan_x
    cam2_pan_y = style.cam2_pan_y
    cam2_rot = style.cam2_rot

    cam2_active = len(cams) > 1 and (
        _c2zoom != "none"
        or style.cam2_zoom_start
        or style.cam2_take_zoom
        or style.cam2_yellow_zoom
        or cam2_fit != 100.0
        or cam2_pan_x != 0.0
        or cam2_pan_y != 0.0
        or cam2_rot != 0.0
        or cam2_cx != 0.5
        or cam2_cy != 0.5
        or bool(style.cam2_head_follow)
    )

    cam2_scale: list[Any] = []
    cam2_holds: list[bool] = []
    cam2scale_js = ""
    cam2_ease_js = ""
    cam2holds_js = ""
    if cam2_active:
        _z2start = style.cam2_zoom_start
        _z2big = style.cam2_zoom_big
        _z2lo = style.cam2_zoom_lo
        _z2hi = style.cam2_zoom_hi
        _z2take: dict[str, Any] | None = None
        if style.cam2_take_zoom or style.cam2_yellow_zoom:
            _z2take = {
                "min_s": style.cam2_take_min,
                "lo": style.cam2_take_lo,
                "hi": style.cam2_take_hi,
                "hold_s": style.cam2_take_hold,
                "out_s": style.cam2_take_out,
                "long_on": bool(style.cam2_take_zoom),
                "yellow_on": bool(style.cam2_yellow_zoom),
            }
            if style.cam2_yellow_zoom:
                _yellow_groups2 = _yellow_phrase_groups(subs, hl, _intro_hl, _fps0)
                _z2take["words"] = [ph for ph, _m in _yellow_groups2]
                _take_yellow(_z2take, _yellow_groups2,
                             _yellow_scores_map(inp.emph, _yellow_need(subs, hl, _intro_hl)),
                             _yellow_strong_on(style, "cam2"),
                             clip_scores=getattr(inp.emph, "scores", None), style=style)
        if _c2zoom == "none":
            if _z2start or _z2take:
                cam2_scale = _cam1_jump_keys(cams, lo=100.0, hi=100.0, fps=meta["fps"], start=_z2start, big=_z2big, take=_z2take, cam=1)
            else:
                cam2_scale = [(0, 100.0)]
        elif _c2zoom == "drift":
            _z2dlo = style.cam2_drift_lo
            _z2dhi = style.cam2_drift_hi
            cam2_scale = _cam1_drift_keys(cams, lo=_z2dlo, hi=_z2dhi, fps=meta["fps"], big=_z2big, start=_z2start, cam=1, take=_z2take)
        elif _c2zoom == "jump":
            cam2_scale = _cam1_jump_keys(cams, lo=_z2lo, hi=_z2hi, fps=meta["fps"], start=_z2start, big=_z2big, take=_z2take, cam=1)
        else:
            cam2_scale = _cam1_zoom_keys(cams, big=_z2big, lo=_z2lo, hi=_z2hi, fps=meta["fps"], start=_z2start, cam=1, take=_z2take)
        # Та же склейка, что у Камеры 1: у кам2 свой список ключей, но правило одно —
        # одна дверь `_merge_same_frame_keys`, второй копии нет.
        cam2_scale = _merge_same_frame_keys(cam2_scale)

        _fit2_k = cam2_fit / 100.0
        if _fit2_k != 1.0:
            cam2_scale = [(f, round(v * _fit2_k, 2), *rest) for f, v, *rest in (cam2_scale or [])]

        cam2_holds = _zoom_key_holds(cam2_scale or [], legacy_hold=(_c2zoom == "jump"))
        cam2scale_js = _jd([([_r(f), _r(v)] + ([int(rest[0])] if rest else []))
                            for f, v, *rest in (cam2_scale or [])])
        cam2_ease_js = _jd(_zoom_key_eases(cam2_scale or []))
        cam2holds_js = _jd([1 if h else 0 for h in cam2_holds])

    cam2_follow_keys: list[tuple[int, int | float]] = []
    if bool(style.cam2_head_follow) and xml_path and len(cams) > 1 and cams[1].get("path"):
        from core import headtrack
        ranges2 = headtrack.cam1_ranges(cams, _fps0, cam=2)
        try:
            hdata2 = headtrack.load_cached(xml_path, cams[1]["path"], ranges=ranges2, cam=2)
        except TypeError:
            hdata2 = headtrack.load_cached(xml_path, cams[1]["path"], cam=2)
        if hdata2 is not None:
            w_src2 = hdata2.get("w") or meta["w"]
            h_src2 = hdata2.get("h") or meta["h"]
            target2 = style.cam2_head_x
            smooth_s2 = style.cam2_head_smooth
            _fit2_k = cam2_fit / 100.0
            min_scale2 = style.cam2_head_min * _fit2_k
            cam2_follow_keys = _cam1_follow_keys(
                cams=cams, pts=hdata2.get("pts", []),
                w_src=w_src2, h_src=h_src2,
                zoom_keys=cam2_scale, holds=cam2_holds,
                fps=_fps0, W=meta["w"], H=meta["h"],
                cx=cam2_cx, pan_x=cam2_pan_x, cam1_fit=100.0,
                target=target2, smooth_s=smooth_s2,
                min_scale=min_scale2,
                cam=2,
            )

    cam2_moved = cam2_active and (cam2_cx != 0.5 or cam2_cy != 0.5 or cam2_pan_x != 0 or cam2_pan_y != 0 or bool(cam2_follow_keys))
    cam2_js = ("" if not cam2_active else
               "\n    var cam2null = nulls.length>1 ? nulls[1] : null;"
               + ("" if not cam2_moved else
                  _anchor_js("cam2null", cam2_cx, cam2_cy, meta["w"], meta["h"], cam2_pan_x, cam2_pan_y))
               + f"\n    var CAM2_SCALE={cam2scale_js};"
               + f"\n    var CAM2_HOLDS={cam2holds_js};"
               + _zoom_keys_js("cam2null", cam2_ease_js, EASE_DEFAULT,
                               scale_name="CAM2_SCALE", holds_name="CAM2_HOLDS",
                               ease_name="CAM2_EASE"))
    # разметка РОТО (дешёвое, без масок — их делает to_ae_full на GPU): сплошная копия
    # персонажа по видимой камере (EDL). Превью может опираться на те же фрагменты.
    # Выключенный ротоскоп — пустая разметка. Флаг тут не спрашивали, и полоса «здесь
    # рото» в предпросмотре оставалась гореть после «Авто-ротоскоп» выкл (жалоба
    # 2026-08-12). Соседняя строка про цензуру флаг спрашивает — здесь его забыли.
    roto_plan = [] if not _roto_on else [
        p for p in _span_roto_plan(cams, 0, int(meta["dur"]), meta["fps"])
                 if cams[p["ci"]].get("path")]       # нужен исходник камеры
    follow_keys = []
    if bool(style.cam1_head_follow) and xml_path and cams and cams[0].get("path"):
        from core import headtrack
        ranges = headtrack.cam1_ranges(cams, _fps0)
        try:
            hdata = headtrack.load_cached(xml_path, cams[0]["path"], ranges=ranges)
        except TypeError:
            hdata = headtrack.load_cached(xml_path, cams[0]["path"])
        if hdata is not None:
            w_src = hdata.get("w") or meta["w"]
            h_src = hdata.get("h") or meta["h"]
            target = style.cam1_head_x
            smooth_s = style.cam1_head_smooth
            # Заполнение уже сидит в ключах зума: сюда 100 — слои клипа и рото кам1
            # заполняют кадр ровно, а fit растит нул вместе с детьми. Порог слежения —
            # в числах пользователя («150 — точка отсчёта для всего, пересчитывать в уме
            # нельзя»), а сравнивается он с ключами, которые уже ×k, — значит и порог ×k.
            min_scale = style.cam1_head_min * _fit_k
            follow_keys = _cam1_follow_keys(
                cams=cams, pts=hdata.get("pts", []),
                w_src=w_src, h_src=h_src,
                zoom_keys=cam1_scale, holds=holds,
                fps=_fps0, W=meta["w"], H=meta["h"],
                cx=cam1_cx, pan_x=pan_x, cam1_fit=100.0,
                target=target, smooth_s=smooth_s,
                min_scale=min_scale,
                # Рамка камеры 1: слой смещён (dx) и увеличен (zoom), зажим краёв —
                # от фактического слоя, а не от центрального (core/frame.frame_shift).
                frame=inp.cam1_frame,
            )

    # fit = 100: заполнение живёт в ключах зума выше, а превью считает ровно так же —
    # (fit/100)·(ключ/100). Вторая копия умножения развела бы превью и AE.
    zoom_plan = {"holds": [1 if h else 0 for h in holds], "fit": 100.0,
                 "cx": cam1_cx, "cy": cam1_cy,
                 "pan": [pan_x, pan_y], "rot": rot,
                 "keys": cam1_scale or [], "ease": _zoom_key_eases(cam1_scale or [])}
    if cam2_active:
        cam2_dict: dict[str, Any] = {
            "cx": cam2_cx,
            "cy": cam2_cy,
            "fit": 100.0,
            "pan": [cam2_pan_x, cam2_pan_y],
            "rot": cam2_rot,
            "keys": cam2_scale,
            "holds": [1 if h else 0 for h in cam2_holds],
            "ease": _zoom_key_eases(cam2_scale),
        }
        if cam2_follow_keys:
            cam2_dict["follow"] = {"keys": [list(k) for k in cam2_follow_keys],
                                   "linear": True}
        zoom_plan["cam2"] = cam2_dict
    if follow_keys:
        zoom_plan["follow"] = {"keys": [list(k) for k in follow_keys],
                               "linear": True}
    # Данные масок для плана: ключи разметки -> имена полей контракта (маски по ним делает
    # to_ae_full, предпросмотр рисует полосу «здесь рото»). Собираются здесь, а не в
    # build.py: иначе у разметки было бы два читателя одной формулы.
    roto = [{"ci": p["ci"], "ts": _r(p["tl_start"]), "te": _r(p["tl_end"]),
             "src_start": _r(p["src_start"]), "src_end": _r(p["src_end"]),
             "scale": _r(p["scale"])} for p in roto_plan]

    # Подстановки шаблона: собираются ТОЛЬКО под фактическое состояние камеры — при
    # дефолтах стиля каждая пустая (или прежняя строка) и .jsx остаётся байт в байт (golden).
    cam1_moved = (cam1_cx != 0.5 or cam1_cy != 0.5 or pan_x != 0 or pan_y != 0 or bool(follow_keys))
    cam1_anchor = ("" if not cam1_moved else
                   ("\n    // точка наезда камеры: anchor+position от неё, "
                    "неподвижна именно она\n"
                    "    if(cam1null){ cam1null.property(\"ADBE Transform Group\").property(\"ADBE Anchor Point\").setValue([%g,%g]);"
                    " cam1null.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([%g,%g]); }"
                    % (cam1_cx * meta["w"] - meta["w"] / 2, cam1_cy * meta["h"] - meta["h"] / 2,
                       cam1_cx * meta["w"] + pan_x, cam1_cy * meta["h"] + pan_y)))
    # Рото привязывается к нулу ПОСЛЕ того, как нул получил якорь точки наезда и ключи зума
    # AE при присвоении parent сохраняет мировое положение слоя и пересчитывает
    # локальную Position ребёнка под трансформ нула на ТЕКУЩИЙ момент — без принудительной
    # позиции рото уезжает на смещение точки наезда от центра кадра (Scale рядом уже
    # перезадаётся по той же причине). При дефолтной точке 0.5/0.5 смещения нет —
    # подстановки пустые, .jsx прежний (golden). Строка одна на обе камеры и ставится
    # БЕЗ условия по камере: локальный ноль верен любому рото, чей нул не сдвинут, а
    # сдвинут ровно тот нул, чья камера и «уехала» (cam1_moved / cam2_moved).
    roto_moved = cam1_moved or cam2_moved
    roto_pos_cc = ("" if not roto_moved else
                   ("\n            // AE компенсирует позицию при привязке по трансформу нула на"
                    "\n            // текущий момент, а нул уже несёт якорь точки наезда и ключи зума"
                    "\n            try{ cc.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([0,0]); }catch(e){}"))
    roto_pos_mk = ("" if not roto_moved else
                   ("\n            try{ mk.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([0,0]); }catch(e){}"))
    cam1_rot_decl = ("\n    var CAM1_ROT=%g;" % rot if rot != 0 else "")
    cam1_rot_cam = (' if(!isSecond){ try{ lay.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' if rot != 0 else "")
    cam2_rot_decl = ("\n    var CAM2_ROT=%g;" % cam2_rot if cam2_rot != 0 else "")
    cam2_rot_cam = (' if(isSecond){ try{ lay.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM2_ROT); }catch(e){} }' if cam2_rot != 0 else "")
    roto_rot_cc = (
        ('\n            if(ci==0){ try{ cc.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' if rot != 0 else "")
        + ('\n            if(ci==1){ try{ cc.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM2_ROT); }catch(e){} }' if cam2_rot != 0 else "")
    )
    roto_rot_mk = (
        ('\n            if(ci==0){ try{ mk.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM1_ROT); }catch(e){} }' if rot != 0 else "")
        + ('\n            if(ci==1){ try{ mk.property("ADBE Transform Group").property("ADBE Rotate Z").setValue(CAM2_ROT); }catch(e){} }' if cam2_rot != 0 else "")
    )
    cam1_follow_decl = ("\n    var CAM1_FOLLOW=%s;" % _jd([list(k) for k in follow_keys])) if follow_keys else ""
    cam1_follow_js = _follow_keys_js("cam1null", "CAM1_FOLLOW", 1) if follow_keys else ""
    cam2_follow_decl = ("\n    var CAM2_FOLLOW=%s;" % _jd([list(k) for k in cam2_follow_keys])) if cam2_follow_keys else ""
    cam2_follow_js = _follow_keys_js("cam2null", "CAM2_FOLLOW", 2) if cam2_follow_keys else ""

    return CameraPlan(
        cam1_scale=cam1_scale or [], holds=holds,
        cam1scale_js=cam1scale_js, cam1_ease_js=cam1_ease_js, cam1holds_js=cam1holds_js,
        roto=roto, zoom=zoom_plan,
        cam1_cx=cam1_cx, cam1_cy=cam1_cy, cam1_moved=cam1_moved,
        cam1_follow_decl=cam1_follow_decl, cam1_follow_js=cam1_follow_js,
        cam1_anchor=cam1_anchor, roto_pos_cc=roto_pos_cc, roto_pos_mk=roto_pos_mk,
        cam1_rot_decl=cam1_rot_decl, cam1_rot_cam=cam1_rot_cam,
        cam2_rot_decl=cam2_rot_decl, cam2_rot_cam=cam2_rot_cam,
        roto_rot_cc=roto_rot_cc, roto_rot_mk=roto_rot_mk, cam2_js=cam2_js,
        cam2_active=cam2_active, cam2_scale=cam2_scale or [], cam2_holds=cam2_holds,
        cam2_follow_decl=cam2_follow_decl, cam2_follow_js=cam2_follow_js)
