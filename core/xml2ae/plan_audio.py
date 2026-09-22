# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Звук плана сцены (задание MU, этап 4 распила scene_plan).

`scene_plan` был одной функцией на 3253 строки; этапы 1–3 (задания MR/MS/MT) вынесли
субтитры в `plan_subs.py`, расчёт интро в `plan_intro.py` и вставки в `plan_inserts.py`.
Четвёртый этап выносит сюда ВЕСЬ звук:

* материалы и события SFX — «поп» жёлтых слов, whoosh и переход на точках смены камеры,
  ризер интро, глитч (по ГРУППАМ подряд идущих глитч-слов) и музыка (случайная из папки
  скачанных или ссылка/путь);
* ключи стиля `<звук>_in/_out/_at/_db` (+ `pop_lead` в кадрах для «попа») — обрезка,
  точка удара и громкость: `_g`, `_sfx_cfg`, `_plain`, `_sfx_off`, `_sfx_tail`, `_sfx_ev`.
  Формула одна: слой ставится так, чтобы точка `at` файла попала на момент события, —
  отсюда `*_place`/`*_tail` для .jsx и готовый старт `t` для плана (JS не пересчитывает);
* данные звука для шаблона — CENSOR, RISER/POP/TRANS/TRANS_SFX, огибающая слоёв глитча
  (`glitch_sfx`) и громкости VOICE_DB/MUSIC_DB/AUDIO_FADE;
* цензура звука — окна мьюта слов со звёздочкой (`_censor_windows` из layout.py) и словарь
  `plan["audio"]` ЦЕЛИКОМ: его читает предпросмотр, второй копии чисел нет.

Перенос ПОСТРОЧНЫЙ: поведение, числа, порядок операций и ТЕКСТ подстановок .jsx не менялись
ни на байт (проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением
.jsx/плана). Имена локальных переменных оставлены как в scene_plan — поэтому тело перенесено
дословно, а входы распаковываются в преамбуле.

Почему дверь одна. Куски звука были разнесены по scene_plan (группы глитч-слов — до
материалов, цензура — после данных вставок, слои глитча — ближе к сборке шаблона), но
зависимого кода между ними нет: всё, что звуку нужно, готово к первой же строке блока.
Вторая дверь завела бы вторую копию общего состояния (группы глитч-слов читают и план,
и шаблон) — ровно то, от чего распил и лечит.

Общее с другими блоками остаётся в build.py и приходит параметрами: `_sv`/`_sv_or`
(дефолты ключей стиля), резолвер ассетов `aset`, точка между этапами `_ckpt` («музыка»)
и `emit`. `_any_glitch` тоже считается в build.py: этот флаг читает ещё и расчёт интро
(plan_intro.py) — второй копии правила нет. `_asset_or` и `_censor_windows` берутся из
jsutil/layout: там они лежат для всех блоков сразу.
"""
import os
from dataclasses import dataclass
from typing import Callable

from .jsutil import _asset_or, _jd, _js, _r
from .layout import _censor_windows


# Звук глитча в секундах (не зависит от fps ролика): огибающая слоя на ГРУППУ глитч-слов
# (ПРАВКА 1/2). Слой стартует за GLITCH_SFX_PRE_S до первого слова группы — это смещение
# снято с эталона (17 кадров при 30 fps) и его не менять. До первого слова — тишина,
# нарастание до glitch_db за GLITCH_SFX_ATTACK_S, полка, затем спад за
# GLITCH_SFX_RELEASE_S до GLITCH_SFX_QUIET_DB; слой кончается через один кадр после
# конца спада (в эталоне зазор 0.013–0.036 с).
GLITCH_SFX_PRE_S = 0.567      # старт слоя за это время до первого слова группы, с
GLITCH_SFX_ATTACK_S = 0.08    # нарастание от тишины до glitch_db у первого слова, с
GLITCH_SFX_HOLD_S = 0.45      # полка звука после последнего слова группы, с
GLITCH_SFX_RELEASE_S = 0.12   # спад до тишины, с
GLITCH_SFX_QUIET_DB = -48.0   # уровень «тихо» (как у микро-фейдов клипов камеры), dB


@dataclass(frozen=True)
class AudioInputs:
    """Вход звукового блока: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan. Лишнего здесь нет: `intro_groups`
    нужны временами глитч-слов, `inserts` — признаком `has_video` (переход и whoosh
    ставятся только по видеовставке), `subs`/`hl` — событиями «попа».
    """
    # Разметка интро группами: из неё берутся времена глитч-слов (по ним — звук глитча).
    intro_groups: list
    # Есть ли в сборке глитч-строка: флаг считается в build.py — его читает и интро.
    any_glitch: bool
    # Подготовленные вставки: тип им уже проставил plan_insert_timings (по файлу), и
    # `has_video` решает, нужны ли переход и whoosh.
    inserts: list
    # Слова субтитров и индексы жёлтых: жёлтое слово становится событием «попа».
    subs: list
    hl: set
    # Точки смены камеры, сек (_cam_change_sec из build.py): по ним ставятся whoosh и
    # переход — второй копии точек нет, их же читают вставки.
    cam_change_sec: list
    # Частота кадров как _fps0 (meta["fps"] or 60): ею считаются кадровые сдвиги.
    fps: float
    # Резолвнутый стиль и обёртки чтения его ключей (общие с другими блоками).
    st: dict
    sv: Callable[[dict, str], object]
    sv_or: Callable[[dict, str], object]
    # Резолвер ассетов (assets.resolver): им ищутся файлы звуков по ролям.
    aset: Callable[[str], object]
    # Галка интро-ризера: стиль уже наложен в build.py, второй копии правила нет.
    intro_riser: bool
    # Музыка: ссылка/путь, галка «случайная из папки», папка музыки и база проекта
    # (папка музыки по умолчанию — рядом с XML). Скачивание — внутри, как и было.
    music: object
    music_random: bool
    music_dir: object
    base: str
    xml_path: str
    # Цензура звука: слова (по звёздочке в них ищутся окна мьюта), галка `censor_audio`
    # и частота как meta["fps"] — ровно то, чем цензор считался в scene_plan.
    censor_source: list
    censor_audio: bool
    censor_fps: float
    # Словарь плана: путь голоса (Камера 1) и громкость музыки — их знает только scene_plan.
    voice_src: str
    music_db: float
    # Точка между этапами («музыка»): показать, где мы, и проверить «Стоп».
    ckpt: Callable[[str], object]
    # Лог: сюда уходят сообщения о музыке и о пропавших файлах звуков.
    emit: Callable[..., object]


@dataclass(frozen=True)
class AudioPlan:
    """Выход звукового блока: ровно те имена, что `scene_plan` читает дальше.

    Файлы звуков уезжают в шаблон подстановками RISER/POP/GLITCH/TRANS/TRANS_SFX,
    `*_place`/`*_tail` — готовый код слоёв (при дефолтах стиля это прежние строки
    шаблона), `audio` — словарь plan["audio"] для предпросмотра.
    """
    riser: str             # файл ризера интро, "" — звука нет (или галка снята)
    pop: str               # файл «попа» жёлтых, "" — нет
    glitch_asset: str      # файл глитча, "" — нет
    glitch_db: float       # громкость глитча, dB (полка огибающей)
    trans: str             # файл перехода, "" — нет (или нет видеовставок)
    trans_sfx: str         # файл whoosh перехода, "" — нет
    sfx_plan: list         # звуки с ГОТОВЫМИ событиями — plan["audio"]["sfx"]
    music_path: str        # выбранная/скачанная музыка, "" — нет
    censor_windows: list   # окна мьюта голоса, сек (сырые, план округляет сам)
    censor_js: str         # те же окна JS-литералом — подстановка CENSOR
    audio: dict            # словарь plan["audio"] целиком (порядок ключей прежний)
    pop_place: str         # где ставить слой «попа» (startTime) — подстановка шаблона
    pop_tail: str          # обрезка/громкость того же слоя
    wsfx_place: str        # whoosh: старт слоя
    wsfx_tail: str         # whoosh: in/out/громкость
    riser_place: str       # ризер: старт слоя
    riser_tail: str        # ризер: in/out/громкость
    trans_place: str       # переход: старт слоя
    trans_tail: str        # переход: in/out/громкость
    glitch_sfx: str        # блок слоёв глитча для .jsx, "" — ставить нечего
    voice_db: float        # громкость голоса, dB: одно число на план и на шаблон
    audio_fade: float      # микро-фейд клипов, с (0.010 при галке audio_fades)


def plan_audio(inp: AudioInputs) -> AudioPlan:
    """Звук плана сцены: SFX, музыка, цензура и подстановки шаблона.

    Тело — дословный перенос блоков из scene_plan (до распила — строки 757-899, 980-981
    и 2138-2179): имена локальных переменных оставлены прежними, поэтому ни одна строка
    не переписана. Порядок операций тот же, и он важен: группы глитч-слов считаются ДО
    материалов (по ним решается, есть ли что ставить), а слои глитча — после событий
    плана: и план, и .jsx берут ОДНИ И ТЕ ЖЕ группы.
    """
    _intro_groups = inp.intro_groups
    _any_glitch = inp.any_glitch
    inserts = inp.inserts
    subs, hl = inp.subs, inp.hl
    _cam_change_sec = inp.cam_change_sec
    _fps0 = inp.fps
    st = inp.st
    # Общие с другими блоками обёртки — по-прежнему в build.py, сюда приходят параметрами.
    _sv, _sv_or = inp.sv, inp.sv_or
    aset = inp.aset
    intro_riser = inp.intro_riser
    music, music_random, music_dir = inp.music, inp.music_random, inp.music_dir
    base, xml_path = inp.base, inp.xml_path
    censor_source, censor_audio = inp.censor_source, inp.censor_audio
    censor_fps = inp.censor_fps
    ckpt, emit = inp.ckpt, inp.emit

    _glitch_word_times = []
    if _any_glitch:
        for _grp in _intro_groups:
            for _ln in _grp:
                if _ln.get("anim") == "glitch":
                    _wds = [str(_w) for _w in (_ln.get("words") or (_ln.get("text") or "").split())]
                    _tms = list(_ln.get("times") or [])
                    for _wi in range(len(_wds)):
                        _wt = float(_tms[_wi]) if _wi < len(_tms) and _tms[_wi] is not None else 0.0
                        if _wt < 0:
                            _wt = 0.0
                        _glitch_word_times.append(_wt)
    # Звук глитча ставится на ГРУППУ слов, а не на слово (ПРАВКА 1): подряд идущие
    # глитч-слова накрыты ОДНИМ растянутым звуком, своя группа начинается там, где
    # следующее слово начинается НЕ раньше конца звука текущей группы (последнее слово
    # группы + полка + спад + кадр). Границы прекомпов при группировке не учитываются —
    # только время: идём по глитч-словам в порядке возрастания.
    _glitch_sound_groups = []
    if _glitch_word_times:
        _frame = 1.0 / _fps0
        for _t in sorted(float(t) for t in _glitch_word_times):
            if (_glitch_sound_groups
                    and _t < _glitch_sound_groups[-1][-1] + GLITCH_SFX_HOLD_S
                    + GLITCH_SFX_RELEASE_S + _frame):
                _glitch_sound_groups[-1].append(_t)
            else:
                _glitch_sound_groups.append([_t])

    riser = aset("intro_riser") if intro_riser else ""
    # Свой файл ризера (задание AA): строка в стиле перекрывает ассет-дефолт.
    _riser_file = (st.get("intro_riser_file") or "").strip()
    if _riser_file and intro_riser:
        riser = _riser_file
    pop = (_asset_or(st.get("pop"), "highlight_pop", aset)) if hl else ""
    glitch_asset = (_asset_or(st.get("glitch"), "glitch", aset)) if _any_glitch else ""
    glitch_db = float(_sv(st, "glitch_db"))
    has_video = any((x.get("type") or "photo") == "video" for x in inserts)
    trans = _asset_or(st.get("transition"), "transition", aset) if has_video else ""
    trans_sfx = _asset_or(st.get("transition_sfx"), "whoosh", aset) if has_video else ""
    # Звуки с обрезкой / точкой удара / громкостью (задание AA). Плоские ключи стиля:
    # <звук>_in/_out/_at/_db (+ pop_lead в кадрах для «попа»). Дефолты = прежнее
    # поведение: .jsx не меняется ни на байт (golden). Формула: слой ставится так,
    # чтобы точка `at` файла попала на момент события (жёлтое слово / кат / старт).
    def _g(v, d):
        return v if v not in (None, "") else d

    def _sfx_cfg(prefix, base_db, def_out):
        return dict(in_s=float(_g(st.get(prefix + "_in"), 0)),
                    out_s=(float(st[prefix + "_out"]) if st.get(prefix + "_out")
                           not in (None, "") else None),
                    at_s=float(_g(st.get(prefix + "_at"), 0)),
                    db=float(_g(st.get(prefix + "_db"), 0)),
                    base=base_db, def_out=def_out)

    pop_cfg = _sfx_cfg("pop", -8.0, 0.1)          # поп по умолчанию обрезан до ~0.1с
    wsfx_cfg = _sfx_cfg("transition_sfx", -10.0, None)
    riser_cfg = _sfx_cfg("intro_riser", 0.0, None)
    trans_cfg = _sfx_cfg("transition", 0.0, None)
    pop_lead = int(_g(st.get("pop_lead"), 4)) or 0

    # Звук задан «как вчера» (все ключи дефолтные) — шаблон работает прежним кодом.
    def _plain(cfg, lead):
        return (cfg["in_s"] == 0 and cfg["at_s"] == 0 and cfg["db"] == 0
                and cfg["out_s"] is None and lead == 4)

    pop_plain = _plain(pop_cfg, pop_lead)
    wsfx_plain = _plain(wsfx_cfg, 4)
    riser_plain = _plain(riser_cfg, 4)
    trans_plain = _plain(trans_cfg, 4)

    def _sfx_off(cfg):
        """Сдвиг старта: момент_события − (at − in), как "+0.2" / "-0.3" / "". """
        off = -(cfg["at_s"] - cfg["in_s"])
        return "%+g" % off if abs(off) > 1e-9 else ""

    def _sfx_tail(cfg, var):
        """JS-хвост после startTime: inPoint / outPoint / громкость. var — имя слоя.
        out не задан — базовая обрезка звука (def_out), как было (поп 0.1)."""
        tail = ""
        if abs(cfg["in_s"]) > 1e-9:
            tail += "\n            try{ %s.inPoint=%s.startTime+%g; }catch(e){}" % (var, var, cfg["in_s"])
        if cfg["out_s"] is not None:
            tail += "\n            try{ %s.outPoint=%s.startTime+%g; }catch(e){}" % (var, var, cfg["out_s"])
        elif cfg["def_out"] is not None:
            tail += "\n            try{ %s.outPoint=%s.startTime+%g; }catch(e){}" % (var, var, cfg["def_out"])
        if abs(cfg["db"]) > 1e-9:
            lv = cfg["base"] + cfg["db"]
            tail += ("\n            try{ %s.property(\"ADBE Audio Group\").property(\"ADBE Audio Levels\")"
                     ".setValue([%g,%g]); }catch(e){}") % (var, lv, lv)
        return tail

    # Токены для шаблона: при дефолтах — прежние JS-строки (.jsx не меняется).
    pop_place = ("pl.startTime=(SUBS[pi][0]+%d)/FPS%s;" % (pop_lead, _sfx_off(pop_cfg))
                 if not pop_plain
                 else "pl.startTime=(SUBS[pi][0]+4)/FPS;  // +4 кадра — звук лучше ложится")
    pop_tail = (_sfx_tail(pop_cfg, "pl") if not pop_plain
                else "try{ pl.outPoint=pl.startTime+0.1; }catch(e){}          // поп обрезан до ~0.1с\n            "
                     "try{ pl.property(\"ADBE Audio Group\").property(\"ADBE Audio Levels\").setValue([-8,-8]); }catch(e){}")
    wsfx_place = ("wl.startTime=cut-TR_IN-TR_SFX_LEAD%s;" % _sfx_off(wsfx_cfg)
                  if not wsfx_plain else "wl.startTime=cut-TR_IN-TR_SFX_LEAD;")
    wsfx_tail = (_sfx_tail(wsfx_cfg, "wl") if not wsfx_plain
                 else "try{ wl.property(\"ADBE Audio Group\").property(\"ADBE Audio Levels\").setValue([-10,-10]); }catch(e){}")
    riser_place = ("rl.startTime=%s;" % (_sfx_off(riser_cfg).lstrip("+") or "0") if not riser_plain else "rl.startTime=0;")
    riser_tail = _sfx_tail(riser_cfg, "rl") if not riser_plain else ""
    trans_place = ("tl.startTime=cut-TR_IN%s;" % _sfx_off(trans_cfg)
                   if not trans_plain else "tl.startTime=cut-TR_IN;")
    trans_tail = _sfx_tail(trans_cfg, "tl") if not trans_plain else ""

    # Звуки в ПЛАН СЦЕНЫ (превью читает их, задание AB): события с ГОТОВЫМ стартом —
    # t (монтажное время, когда звук начинает играть = ev − at + in), файловые in/out.
    # JS старт не пересчитывает (задание AB): берёт числа из плана.
    def _sfx_ev(ev, cfg):
        return {"t": round(ev - cfg["at_s"] + cfg["in_s"], 3),
                "in": cfg["in_s"], "out": cfg["out_s"]}

    _hl_events = [s / _fps0 for k, (s, e, w) in enumerate(subs) if k in hl] if hl else []
    sfx_plan = []
    if pop and _hl_events:
        sfx_plan.append({"kind": "pop", "media": pop,
                         "events": [_sfx_ev(t + pop_lead / _fps0, pop_cfg)
                                    for t in _hl_events],
                         "db": pop_cfg["db"], "base": pop_cfg["base"]})
    if trans_sfx:
        sfx_plan.append({"kind": "whoosh", "media": trans_sfx,
                         "events": [_sfx_ev(c - 0.386 - 0.083, wsfx_cfg)
                                    for c in _cam_change_sec],
                         "db": wsfx_cfg["db"], "base": wsfx_cfg["base"]})
    if trans:
        sfx_plan.append({"kind": "transition", "media": trans,
                         "events": [_sfx_ev(c - 0.386, trans_cfg)
                                    for c in _cam_change_sec],
                         "db": trans_cfg["db"], "base": trans_cfg["base"]})
    if riser:
        sfx_plan.append({"kind": "riser", "media": riser,
                         "events": [_sfx_ev(0.0, riser_cfg)],
                         "db": riser_cfg["db"], "base": riser_cfg["base"]})
    if glitch_asset and _glitch_word_times:
        sfx_plan.append({"kind": "glitch", "media": glitch_asset,
                         "events": [{"t": round(max(0.0, t - 0.3), 3),
                                     "in": 0.0, "out": 1.05}
                                    for t in _glitch_word_times],
                         "db": glitch_db, "base": 0.0})
    music_path = ""
    _mdir = music_dir or os.path.join(base, "music")   # папка музыки (по умолчанию рядом с XML)
    if music_random or music:
        ckpt("музыка")
    if music_random:                                   # случайно из уже скачанных
        from core import ytmusic
        music_path = ytmusic.random_track(_mdir, emit=emit, seed=xml_path) or ""
    elif music:                                        # ссылка YouTube (скачать) или путь к файлу
        from core import ytmusic
        music_path = ytmusic.resolve(music, _mdir, emit=emit)

    # Окна мьюта голоса (цензура): слова со звёздочкой — их ставит censor.py ещё при сборке
    # XML. Источник — `censor_source`: цензор считается по ВСЕМ словам (интро-слова звучат),
    # и fps здесь тот же meta["fps"], что и был, а не _fps0.
    censor_windows = _censor_windows(censor_source, censor_fps) if censor_audio else []
    censor_js = _jd([[_r(a), _r(b)] for a, b in censor_windows])

    if _any_glitch and glitch_asset and _glitch_word_times:
        gl_blocks = []
        eff_db = 0.0 + glitch_db
        _r4 = lambda v: round(v * 10000) / 10000
        for _grp_t in _glitch_sound_groups:
            # Один слой звука на ГРУППУ глитч-слов (ПРАВКА 1): startTime за 0.567 до
            # ПЕРВОГО слова группы, дальше огибающая — тишина до первого слова, полка
            # на glitch_db, спад за кадр до конца слоя (ПРАВКА 2).
            _t_first, _t_last = _grp_t[0], _grp_t[-1]
            st_val = _r4(_t_first - GLITCH_SFX_PRE_S)
            in_val = _r4(_t_first)
            out_val = _r4(_t_last + GLITCH_SFX_HOLD_S + GLITCH_SFX_RELEASE_S + 1.0 / _fps0)
            atk_val = _r4(_t_first + GLITCH_SFX_ATTACK_S)
            rel_s_val = _r4(_t_last + GLITCH_SFX_HOLD_S)
            rel_e_val = _r4(_t_last + GLITCH_SFX_HOLD_S + GLITCH_SFX_RELEASE_S)
            lines = [
                '            var gl=main.layers.add(glitchItem); gl.name="Глитч";',
                '            gl.startTime=%g; gl.inPoint=%g; gl.outPoint=%g;' % (st_val, in_val, out_val),
                '            try{ var glAlv=gl.property("ADBE Audio Group").property("ADBE Audio Levels");',
                '                 glAlv.setValue([%g, %g]);' % (eff_db, eff_db),
                '                 // звук покрывает ВСЮ группу глитч-слов (ПРАВКА 1): тишина до',
                '                 // первого слова, нарастание за 0.08, полка, спад до тишины',
                '                 // за кадр до конца слоя',
                '                 glAlv.setValueAtTime(%g, [%g, %g]);' % (in_val, GLITCH_SFX_QUIET_DB, GLITCH_SFX_QUIET_DB),
                '                 glAlv.setValueAtTime(%g, [%g, %g]);' % (atk_val, eff_db, eff_db),
                '                 glAlv.setValueAtTime(%g, [%g, %g]);' % (rel_s_val, eff_db, eff_db),
                '                 glAlv.setValueAtTime(%g, [%g, %g]); }catch(e){}' % (rel_e_val, GLITCH_SFX_QUIET_DB, GLITCH_SFX_QUIET_DB),
            ]
            gl_blocks.append("\n".join(lines))
        gl_body = "\n".join(gl_blocks)
        glitch_sfx = (
            '\n\n    // ---- звук глитча: один слой на ГРУППУ глитч-слов (ПРАВКА 1) ----\n'
            f'    var GLITCH={_js(glitch_asset)};\n'
            '    if (GLITCH){ var glitchItem=imp(GLITCH);\n'
            '        if (glitchItem){\n'
            '            toBin(glitchItem,"Интро");\n'
            f'{gl_body}\n'
            '        }\n'
            '    }'
        )
    else:
        glitch_sfx = ""

    # Громкость голоса и микро-фейд клипов: одни числа на план и на шаблон — читаются
    # здесь ОДИН раз (раньше voice_db читался и в плане, и в подстановке).
    voice_db = float(_sv_or(st, "voice_db"))
    audio_fade = (0.010 if _sv(st, "audio_fades") else 0.0)

    # plan["audio"] собирается здесь целиком: числа звука и его данные для превью —
    # из одного места, порядок ключей прежний (побайтовое сравнение плана).
    audio = {"voice_src": inp.voice_src,
             "voice_db": voice_db,
             "music_path": music_path, "music_db": inp.music_db,
             "censor": [[_r(a), _r(b)] for a, b in censor_windows],
             "sfx": sfx_plan}
    return AudioPlan(
        riser=riser, pop=pop, glitch_asset=glitch_asset, glitch_db=glitch_db,
        trans=trans, trans_sfx=trans_sfx, sfx_plan=sfx_plan, music_path=music_path,
        censor_windows=censor_windows, censor_js=censor_js, audio=audio,
        pop_place=pop_place, pop_tail=pop_tail,
        wsfx_place=wsfx_place, wsfx_tail=wsfx_tail,
        riser_place=riser_place, riser_tail=riser_tail,
        trans_place=trans_place, trans_tail=trans_tail,
        glitch_sfx=glitch_sfx, voice_db=voice_db, audio_fade=audio_fade)
