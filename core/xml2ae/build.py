# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Сборка .jsx: to_ae_full (один таймлайн) и build_combined (несколько в один файл).

Здесь же virtual_edl — тот же разбор, но для черновика ffmpeg, без AE.
"""
import os
import re
from core import paths
from core.app_meta import console_emit, wrap_emit
from core import fonts as _fonts
from core import styles as _styles
from core.fileio import atomic_text_write

from .jsutil import _fill_js, _jd, _js, _js_multiline, _r
from .layout import (DEFAULT_DISCLAIMER, DISC_FIT_W, EASE_DEFAULT, HL_DUR, HL_EASE_IN, HL_EASE_OUT,
                     INS_EXIT, INS_RISE_ENTER,
                     INTRO_F_DUR, INTRO_FIT_W, INTRO_LINE_STEP, INTRO_SCALE,
                     SHADE_BLUR, SHADE_DY, SHADE_H, SHADE_OX, SHADE_OY, SHADE_REF_W, SHADE_SCALE,
                     SHADE_W, SHADE_X,
                     SUB_BG_SH_DIR, SUB_BG_SH_DIST,
                     SUB_BG_SH_OP, SUB_BG_SH_SOFT,
                     cover_sweep,
                     _cam_change_frames,
                     _ins_enter_exit, _ins_scale,
                     _media_dims, _project_base, _stack_layout,
                     _zoom_max)
from .parse import Cancelled, HERE, parse_full
# Числа огибающей звука глитча переехали в plan_audio.py (этап 4 распила scene_plan), но
# остаются контрактом сборки: их берут снаружи (tests/test_intro_anims_and_glitch_sound.py)
# по-прежнему из build — второй копии чисел нет, это тот же объект.
from .plan_audio import (GLITCH_SFX_ATTACK_S, GLITCH_SFX_HOLD_S,  # noqa: F401
                         GLITCH_SFX_PRE_S, GLITCH_SFX_QUIET_DB, GLITCH_SFX_RELEASE_S,
                         AudioInputs, plan_audio)
from .plan_camera import CameraInputs, plan_camera
from .plan_inserts import (InsertTimingInputs, InsertsInputs, plan_insert_timings,
                           plan_inserts)
from .plan_intro import IntroInputs, _g_at, _grp_big_i, plan_intro
from .plan_intro_tpl import IntroTplInputs, plan_intro_tpl
from .plan_subs import SubsInputs, plan_subs
from .template import AE_FULL

# Цвет камер через Lumetri (задание ZJ): ключ плана -> matchName эффекта в AE и подпись
# для лога. Номера сняты архитектором с живого AE 26.2 по свойствам эффекта (ADBE Lumetri),
# диапазоны ползунков — в core/style_schema.py. Порядок = порядок setValue в .jsx.
LUMETRI_PARAMS = (
    ("exposure", "ADBE Lumetri-0011", "Exposure"),
    ("contrast", "ADBE Lumetri-0012", "Contrast"),
    ("highlights", "ADBE Lumetri-0013", "Highlights"),
    ("shadows", "ADBE Lumetri-0014", "Shadows"),
    ("whites", "ADBE Lumetri-0015", "Whites"),
    ("blacks", "ADBE Lumetri-0016", "Blacks"),
    ("temp", "ADBE Lumetri-0007", "Temperature"),
    ("tint", "ADBE Lumetri-0008", "Tint"),
    ("sat", "ADBE Lumetri-0020", "Saturation"),
)


# Tritone выбеливает яркие цвета: при яркости цвета мидтонов (Rec.709,
# 0.2126R+0.7152G+0.0722B по значениям 0–1) выше порога не ставим. Замер 2026-09-18:
# жёлтый интро Джаггера 0.91, жёлтый по умолчанию 0.87 — выкл; голубой 0.61,
# оранжевый 0.57, красный 0.24 — вкл.
# Механика: Tritone красит по яркости — цвет мидтонов уезжает на букву, Highlights
# остаётся белым. Свечение выталкивает букву почти в белое, и она попадает в Highlights
# вместо мидтонов (задание ZN). Свечение (Glo2) при этом не трогаем.
TRITONE_MAX_LUM = 0.7


def _tritone_on(rgb):
    """Ставить ли ADBE Tritone для цвета мидтонов rgb ([r,g,b] 0..1; None — дефолтный жёлтый).

    Порог и почему он есть — в комментарии к TRITONE_MAX_LUM. Дефолт None повторяет
    подстановку _fill_js(None): тот же жёлтый, что и при пустом hl_fill в стиле."""
    r = list(rgb or [1, 0.9176, 0])[:3]
    while len(r) < 3:
        r.append(0.0)
    return (0.2126 * r[0] + 0.7152 * r[1] + 0.0722 * r[2]) <= TRITONE_MAX_LUM


def _sv(st, key):
    """Значение ключа стиля; ключа нет (None) — дефолт из styles.BASE (задание JC).

    Раньше запасное число стояло рядом с КАЖДЫМ чтением (`st.get("sub_bg_op") if
    st.get("sub_bg_op") is not None else 72.0`), и правка дефолта в styles.BASE до
    сборки не доезжала: в BASE новое число, в .jsx старое. Источник дефолтов один —
    styles.BASE. Форма с `is not None`: ноль и пустая строка — ЗАДАННЫЕ значения.
    """
    v = st.get(key)
    return _styles.BASE[key] if v is None else v


def _sv_or(st, key):
    """Значение ключа стиля; пусто/ноль — дефолт из styles.BASE (задание JC).

    Форма `st.get(k) or <число/строка>`: ноль, пустая строка и None означают «не
    задано» ровно как раньше, но запас берётся из styles.BASE, а не из литерала
    рядом с чтением.
    """
    return st.get(key) or _styles.BASE[key]


def _fps_js(fps):
    """Частота для строки `FPS=` в шаблоне. В шаблоне стояло `%d`, и NTSC-частота
    29.97 усекалась до 29 — кадры XML делились бы на 29, то есть на 3.2% быстрее
    реального времени. Целая частота печатается ровно как раньше (`60`, эталон .jsx
    не меняется), дробная — числом с 6 знаками после запятой (`29.97003`)."""
    f = float(fps)
    return ("%d" % f) if f.is_integer() else str(round(f, 6))


def _sub_bg_expr(st, sub_layer_name="Субтитры (текст)"):
    """Выражение на размер плашки субтитров (задание DE, обновлено DL).

    Текст берётся целиком из refs/sub_bg_size.js, подставляются 4 константы из стиля
    и имя слоя субтитров в главном композе.
    """
    h = float(_sv(st, "sub_bg_h"))
    pad = float(_sv(st, "sub_bg_pad")) / 100.0
    padmin = float(_sv(st, "sub_bg_padmin"))
    anim = float(_sv(st, "sub_bg_anim"))
    ref_path = paths.data("refs", "sub_bg_size.js")
    with open(ref_path, "r", encoding="utf-8") as f:
        src = f.read()
    marker = "// --- НАСТРОЙКИ ---"
    idx = src.find(marker)
    if idx != -1:
        src = src[idx:]
    src = re.sub(r"const\s+fixedHeight\s*=\s*[^;]+;", f"const fixedHeight  = {h:g};", src)
    src = re.sub(r"const\s+padPercent\s*=\s*[^;]+;", f"const padPercent   = {pad:g};", src)
    src = re.sub(r"const\s+minPadX\s*=\s*[^;]+;", f"const minPadX      = {padmin:g};", src)
    src = re.sub(r"const\s+animDuration\s*=\s*[^;]+;", f"const animDuration = {anim:g};", src)
    src = src.replace('const precompLayer = thisComp.layer("Субтитры (текст)");', f'const precompLayer = thisComp.layer({_js(sub_layer_name)});')
    return src


def _caption_bg_size_expr(kx, ky):
    """Выражение на «Размер прямоугольника» плашки под подписью (задание DG, обновлено DL)."""
    ref_path = paths.data("refs", "caption_bg_size.js")
    with open(ref_path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = [ln for ln in src.splitlines() if not ln.startswith("//")]
    src = "\n".join(lines).strip()
    src = src.replace('targetLayerName = "textlayer1";', 'targetLayerName = "Подпись";')
    src = re.sub(
        r'\[r\.width\s*\*\s*[\d.]+\s*,\s*r\.height\s*\*\s*[\d.]+\];[^\n]*',
        f'[r.width * {kx:g}, r.height * {ky:g}];',
        src,
    )
    return src


def _caption_pos_expr(cap_x, cap_y, kx):
    """Выражение на позицию ТЕКСТА подписи: caption_x — ЛЕВЫЙ КРАЙ блока (задание DL).

    Текст берётся из refs/caption_pos.js, подставляются три константы из стиля.
    Ширину плашки AE знает только при отрисовке, поэтому центр надписи считается
    выражением: левый край + половина ширины плашки.
    """
    ref_path = paths.data("refs", "caption_pos.js")
    with open(ref_path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = [ln for ln in src.splitlines() if not ln.startswith("//")]
    src = "\n".join(lines).strip()
    return re.sub(r"const CAP_X = [^;]+;",
                  f"const CAP_X = {cap_x:g}, CAP_Y = {cap_y:g}, KX = {kx:g};", src)


def _caption_bg_pos_expr():
    """Выражение на позицию плашки под подписью (задание DG) — центрирование по тексту."""
    ref_path = paths.data("refs", "caption_bg_pos.js")
    with open(ref_path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = [ln for ln in src.splitlines() if not ln.startswith("//")]
    return "\n".join(lines).strip()


def _accent_word(w, case):
    """Регистр слова — единая машинка для акцента интро (задание R) и регистра субтитров
    (задание CO). Один источник трансформации: .jsx и план читают готовый текст, второй
    копии правила не заводится.
    title/sentence — Заглавная первая, остальные строчные («Сдо*нуть» из «СДО*НУТЬ»);
    as-is — не трогаем; upper — всё заглавное; lower — всё строчное."""
    s = str(w)
    if case == "upper":
        return s.upper()
    if case == "lower":
        return s.lower()
    if case == "as-is":
        return s
    return s[:1].upper() + s[1:].lower() if s else s


def _intro_line_font(line, intro_font_ps, intro_hl_font_ps):
    """Шрифт строки интро — ЕДИНСТВЕННАЯ лесенка (задание BP): акцентный перекрывает;
    жёлтая строка — intro_hl_font, иначе intro_font. Её же используют автофит и
    plan.intro[].fonts; превью своей лесенки не держит (жёлтые строки рисовались
    системным шрифтом и выходили на 41 % шире, чем в AE)."""
    af = (line.get("accent_font") or "").strip()
    if af:
        return af
    if line.get("color") == "yellow":
        return intro_hl_font_ps
    return intro_font_ps


def _intro_fit_ds(lines, ts, te, ds, w, G, cam_keys, fps, st, intro_font_ps,
                  intro_hl_font_ps, fsize, holds=None, hold=None, big_w=None,
                  fit_w=None, both_ways=False, fit_max=None):
    """Автофит группы интро (задание BP): строка видна как lineW·(iSc/100)·G·Z
    (iSc = INTRO_SCALE·ds/100 — масштаб прекомпа, G — общий масштаб интро, Z — зум
    Камеры 1), и ds подбирается так, чтобы эта ширина была ровно fit_w·W.
    Применяется, только когда gs == 100 (задание CF: рука сильнее автофита — если
    gs != 100, группу масштабировали вручную).
    st нужен для back_scale; шрифты приходят готовыми (intro_font_ps/intro_hl_font_ps),
    свою лесенку автофит не заводит — иначе измерит не тот шрифт, что уйдёт в AE.
    Шрифт не найден — ширины нет, группу не трогаем: подгонять по неизвестной ширине
    хуже, чем не трогать.

    big_w — ширина ВСЕГО блока группы с большой строкой (total из intro_big_layout,
    задание ZY): у такой группы по горизонтали видно не самую длинную строку, а блок
    «большое слово + зазор + стопка», и автофит обязан мерить именно его. None (группа
    без большой строки) — прежний максимум по строкам.

    fit_w — доля ширины кадра (0…1), под которую подгоняем; None — INTRO_FIT_W (дефолт
    ПРИВЯЗАННОГО интро).

    both_ways (задание MI) — интро откреплено от камеры (intro_cam=False): увеличивать
    его некому (зум Камеры 1 в расчёт не входит, cam_keys пуст), поэтому группа садится
    на ширину в ОБЕ стороны — ds = fit, и короткая строка растягивается, как раньше её
    растягивал зум. Привязанное (both_ways=False) по-прежнему только ужимается: потолок
    ему задаёт ручной gs, а ширину — зум камеры.

    fit_max — «Потолок увеличения интро, %» (задание MO), проценты (100…1000):
    ds = min(fit, fit_max) у откреплённого интро. Режется только УВЕЛИЧЕНИЕ: ужатие
    длинной строки потолком не ограничивается (fit < 100 при любом потолке ≥ 100).
    None — потолка нет (привязанное интро ручку не читает вовсе). Без потолка одно
    короткое слово раздувалось до 667–819 % (≈780 px высотой)."""
    if not lines:
        return ds
    linew = 0.0
    if big_w is not None:
        linew = float(big_w)
    else:
        for ln in lines:
            ps = _intro_line_font(ln, intro_font_ps, intro_hl_font_ps)
            fs = round(fsize * float(_sv(st, "back_scale"))) if ln.get("back") else fsize
            wpx = _fonts.text_width(ps, " ".join(ln.get("words") or []), fs)
            if wpx is None:
                return ds
            linew = max(linew, wpx)
    z = _zoom_max(cam_keys, fps, ts, te, holds=holds, hold=hold)
    _fw = INTRO_FIT_W if fit_w is None else float(fit_w)
    fit = 100.0 * w * _fw / (linew * (INTRO_SCALE / 100.0) * G * (z / 100.0))
    if both_ways:
        # Потолок увеличения (задание MO): min(fit, потолок) — режет рост, ужатие нет.
        return fit if fit_max is None else min(fit, float(fit_max))
    return min(ds, fit)


def _parse_intro_count(text, raw_dec=None):
    """Разбор целевого числа для anim=='count' в интро.
    Числом считается текст, состоящий из цифр, возможно с ОДНИМ разделителем
    дробной части — запятой или точкой, и возможно с пробелами-разделителями тысяч.
    Возвращает (target, dec, expr, sep) или None, если текст не число.
    """
    if not text:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip()
    if not s:
        return None
    dots = s.count(".")
    commas = s.count(",")
    if dots + commas > 1:
        return None
    sep = "." if dots == 1 else ("," if commas == 1 else None)
    if sep:
        int_part, frac_part = s.split(sep)
        int_clean = int_part.replace(" ", "")
        if (
            not int_clean
            or not int_clean.isascii()
            or not int_clean.isdigit()
            or not frac_part
            or not frac_part.isascii()
            or not frac_part.isdigit()
        ):
            return None
        auto_dec = len(frac_part)
        try:
            val = float(int_clean + "." + frac_part)
        except ValueError:
            return None
    else:
        int_clean = s.replace(" ", "")
        if not int_clean or not int_clean.isascii() or not int_clean.isdigit():
            return None
        auto_dec = 0
        try:
            val = int(int_clean)
        except ValueError:
            return None

    # Автоподсчёт знаков после запятой по числу: целое -> 0, 2.5 или 2,5 -> 1 знак
    dec = auto_dec

    if dec == 0:
        expr = 'Math.round(effect("Slider Control")("Slider"))'
    else:
        if sep == ".":
            expr = f'(effect("Slider Control")("Slider")).toFixed({dec})'
        else:
            expr = f'(effect("Slider Control")("Slider")).toFixed({dec}).replace(".", ",")'

    target = int(val) if (isinstance(val, float) and val.is_integer() and dec == 0) else val
    return target, dec, expr, sep


def _intro_cnt_positions(x, nwords):
    """Позиции слов строки, на которых стоит счётчик (cnt_words), по возрастанию.

    cnt_words — новый формат (список позиций внутри строки). Мусор, дроби и выход за
    границы строки молча пропускаем: строка без единого разобранного числа остаётся
    без счётчика. Пустой список — пустой результат (счётчиков нет).
    """
    cw = x.get("cnt_words")
    if not isinstance(cw, list):
        return []
    out = set()
    for v in cw:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        try:
            p = int(v)
        except (ValueError, OverflowError):    # NaN/бесконечность из чужого JSON — не позиция
            continue
        if p != v or not (0 <= p < nwords):
            continue
        out.add(p)
    return sorted(out)


def _has_valid_count(x):
    if isinstance(x.get("cnt_words"), list):
        wds = [str(wd) for wd in (x.get("words") or (x.get("text") or "").split())]
        for p in _intro_cnt_positions(x, len(wds)):
            if _parse_intro_count(wds[p], x.get("dec")) is not None:
                return True
        return False
    if not (x.get("is_count") or x.get("anim") == "count"):
        return False
    wds = [str(wd) for wd in (x.get("words") or (x.get("text") or "").split())]
    for wd in wds:
        if _parse_intro_count(wd, x.get("dec")) is not None:
            return True
    return _parse_intro_count(" ".join(wds).strip(), x.get("dec")) is not None


# Настройки плагина Deep Glow 2 для слов жёлтого глитча интро (задание HD,
# снято архитектором из MKnew13.aep пользователя, 2026-09-12, 30 потоков со значением,
# одинаковые во всех 28 копиях; порядок — как в слое AE, чтобы списки вроде
# пресета качества ставились раньше зависимых значений).
DEEP_GLOW2_GLITCH = [
    ("PEDG2-0017", 835), ("PEDG2-0018", 0.03), ("PEDG2-0107", 3), ("PEDG2-0118", 10),
    ("PEDG2-0119", 90), ("PEDG2-0042", [1, 0, 0, 1]), ("PEDG2-1048", 3), ("PEDG2-0049", 75),
    ("PEDG2-0050", 0.5), ("PEDG2-0051", 8), ("PEDG2-0053", 1), ("PEDG2-0155", 0),
    ("PEDG2-0131", 50), ("PEDG2-0136", 3), ("PEDG2-0132", 0), ("PEDG2-0057", 0),
    ("PEDG2-0114", 0), ("PEDG2-0115", 0), ("PEDG2-0137", 0), ("PEDG2-0058", 2),
    ("PEDG2-0102", 1), ("PEDG2-0109", 0), ("PEDG2-0143", 1), ("PEDG2-0146", 1),
    ("PEDG2-0059", 0), ("PEDG2-0060", 0), ("PEDG2-0061", 0), ("PEDG2-0062", 0),
    ("PEDG2-0027", 0), ("PEDG2-1107", 6),
]


# Параметры анимаций интро (глитч, раскрытие): сборка .jsx берёт числа
# из этого словаря, а план сцены (scene_plan) передаёт их в превью браузера.
# Единый источник истины — вторая копия в JS не заводится.
INTRO_ANIMS = {
    "glitch": {
        "dur": 0.44,
        # сила Gaussian Blur на слое слова глитча (было 6.8, пользователь 2026-09-11: вдвое слабее)
        "blur": 3.4,
        "end_keys": [
            [0.017, 100],
            [0.205, 5],
            [0.392, 100],
        ],
        "op_keys": [
            [0.0, 0],
            [0.05, 100],
            [0.1, 100],
            [0.1417, 0],
            [0.1833, 93],
            [0.225, 0],
            [0.2667, 100],
        ],
    },
    "reveal": {
        "dur": 0.44,
        "blur": 26.8,
        "scale": 0.7,
        "scale_3d": [11, 11, 91.66667],
        "shape": 2,
        "smoothness": 100,
        "ease": [10, 95],
    },
}


def _intro_appear_dur(anim, count=False):
    """Сколько играет появление слова интро, с (задание MH) — по ветвям introAnimFX.

    Глитч — своя длительность INTRO_ANIMS (её же ставят селектору текста и ключам
    Opacity); счётчик — HL_DUR (слайдер крутится до него, появление длится F_DUR, берём
    большее); всё остальное (фейд, масштаб, раскрытие, up/left/right) — F_DUR. Числа
    здесь не свои: это ровно те подстановки, что уезжают в introAnimFX.
    """
    if anim == "glitch":
        return float(INTRO_ANIMS["glitch"]["dur"])
    if count:
        return float(HL_DUR)
    return float(INTRO_F_DUR)


def _lumetri_decl(lum):
    """Объявление LUMETRI и функции applyLumetri для .jsx (задание ZJ).

    Один эффект ADBE Lumetri на слой, значения — по matchName из LUMETRI_PARAMS;
    ошибки уходят в _LOG (пустых catch нет: иначе неверный цвет ищут в AE вслепую).
    При выключенной галке стиля (lum = None) подстановка пустая — .jsx побайтово
    прежний (golden держит).
    """
    if not lum:
        return ""
    # Начинается без ведущего \n и кончается \n: подстановка стоит в НАЧАЛЕ строки
    # шаблона (перед `var ROTO=`) — при выключенной галке строка шаблона не меняется.
    js = ["    // Цвет камер через Lumetri (задание ZJ): значения из стиля, exposure уже"
          "\n    // включает экспозицию клипа. Один эффект на слой — как в панели Lumetri в AE.",
          "\n    var LUMETRI = {%s};"
          % ", ".join('%s: %g' % (k, lum[k]) for k, _mn, _lb in LUMETRI_PARAMS),
          "\n    function applyLumetri(L){",
          "\n        try{",
          "\n            var lc = L.property(\"ADBE Effect Parade\").addProperty(\"ADBE Lumetri\");"]
    for _key, _mn, _label in LUMETRI_PARAMS:
        js.append("\n            try{ lc.property(\"%s\").setValue(LUMETRI.%s); }"
                  "catch(e){ _LOG(\"Lumetri %s: \" + e); }" % (_mn, _key, _label))
    js.append("\n        }catch(e){ _LOG(\"Lumetri на слое: \" + e); }")
    js.append("\n    }\n")
    return "".join(js)


# Клипы камер: покадровая экспозиция (EXPOSURE) ИЛИ весь Lumetri из стиля — ровно в том
# же месте шаблона, что и раньше. Значения по умолчанию — прежний текст .jsx байт в байт.
LUMETRI_CAM_OFF = (
    "if (EXPOSURE!=0){ try{ var lc=lay.property(\"ADBE Effect Parade\").addProperty(\"ADBE Lumetri\");  // яркость на все камеры\n"
    "                try{ lc.property(\"ADBE Lumetri-0011\").setValue(EXPOSURE); }catch(e){} }catch(e){} }")
LUMETRI_CAM_ON = "applyLumetri(lay);"
# Рото-копии камер — то же самое, но своей строкой шаблона (без внешнего try).
LUMETRI_ROTO_OFF = (
    "if (EXPOSURE!=0){ try{ var lc=cc.property(\"ADBE Effect Parade\").addProperty(\"ADBE Lumetri\");\n"
    "                lc.property(\"ADBE Lumetri-0011\").setValue(EXPOSURE); }catch(e){} }")
LUMETRI_ROTO_ON = "applyLumetri(cc);"


def scene_plan(xml_path, cam1_scale=None,   # None -> авто по сменам кам1→кам2
               music=None, music_db=-20.0, music_dir=None, base=None,
               disclaimer=DEFAULT_DISCLAIMER, disc_sec=1.35, intro_riser=True,
               highlights=None, hl_breaks=None, hl_count=None, hl_joins=None, inserts=None, censor_audio=True, intro=None,
               intro_remove=None, intro_splits=None, ncams=None, exposure=0.0, intro_mode="word",
               roto=False, roto_bottom=0.0, roto_device=None, style=None,
               music_random=False, emit=None,
               include_xml_inserts=True, cancel=None, word_timings=None,
               caption=None, glitch_glow="builtin"):
    """ПЛАН СЦЕНЫ (задание C): вся арифметика сборки, без записи .jsx и без GPU.
    to_ae_full рендерит из него шаблон после рото-масок; предпросмотр (задание D)
    читает план напрямую. Поля — контракты JSX-структур (CAM/SUBS/INSERTS/INTRO_GROUPS)
    и производные: inserts[].anim (готовые ключи анимаций вставок — раньше их считал
    ExtendScript, остаток задания B), roto — разметка РОТО-фрагментов (маски делает
    to_ae_full, GPU), zoom.keys/ease — ключи Камеры 1. cancel — колбэк «нажали Стоп?»
    (см. Cancelled); свой emit — туда, где раньше печатали в консоль."""
    _stop = cancel or (lambda: False)
    emit = wrap_emit(emit)

    def _ckpt(stage):
        """Точка между этапами: показать, где мы, и проверить «Стоп»."""
        if stage:
            emit("  · {stage}", stage=stage)
        if _stop():
            raise Cancelled()

    _ckpt("разбор XML")
    meta, cams, subs, xml_inserts = parse_full(xml_path, ncams=ncams)
    if not cams:
        # ValueError, а НЕ SystemExit: вызывающие ловят только Exception, поэтому
        # SystemExit пролетал сквозь них — /api/to_ae отдавал 500-HTML вместо {error},
        # а фоновая сборка молча писала «Сборка завершена» с пустым results.
        raise ValueError("Не нашёл видеодорожки с камерами в XML.")
    for _ci, _c in enumerate(cams):                 # диагностика 1-кам «нет названий нулов»: пустой путь
        if not (_c.get("path") or "").strip():
            emit("⚠ Камера {cam} без пути к файлу (нул создастся пустым — проверь XML).", cam=_ci + 1)
    _fp = meta["fps"]
    inserts = [dict(x) for x in (inserts or [])]       # копии: не мутируем словари вызывающего
    if include_xml_inserts:
        inserts += [dict(
            type=xi["type"], style=xi.get("style") or "cam2", media=xi["media"],
            start_s=xi["start"] // _fp, start_f=xi["start"] % _fp,
            dur_s=(xi["end"] - xi["start"]) // _fp, dur_f=(xi["end"] - xi["start"]) % _fp,
            scale=_ins_scale(xi["media"], xi.get("style") or "cam2"), mosaic=False,
            sin=xi.get("sin", 0) / _fp)                # source in-point в секундах
            for xi in xml_inserts]
    from core import styles as _styles  # пресет стиля (шрифт/цвет/звуки/рото/вставки)
    st = _styles.resolve(style)
    if st.get("disclaimer") is not None:               # стиль переопределяет дисклеймер ("" = скрыть)
        disclaimer = st["disclaimer"]
    if st.get("intro_riser") is not None:              # стиль может отключить интро-SFX (ризер)
        intro_riser = bool(st["intro_riser"])
    _fps0 = meta["fps"] or 60

    def _active_cam_at(t_sec):
        """Индекс камеры, реально показываемой в момент t (сек): верхняя включённая
        перебивка, иначе Камера 1. Как camAt в JSX."""
        f = t_sec * _fps0 + 1e-4
        best = 0
        for ci in range(len(cams)):
            for cl in cams[ci]["clips"]:
                if cl[4] and cl[0] <= f < cl[1]:
                    best = ci
                    break
        return best

    # Точки СМЕНЫ КАМЕРЫ (сек): где показываемая (верхняя включённая) камера меняется. Не каждый
    # VAD-стык внутри одной камеры, а именно переход кам1↔кам2. По ним режем/прижимаем вставки
    # (plan_inserts.py, задание MT) и ставим звуки переходов (plan_audio.py, задание MU) — точки
    # считаются здесь ОДИН раз, второй копии правила нет.
    _cam_change_sec = [f / _fps0 for f in _cam_change_frames(cams)]
    # ---- Вставки: тайминги вынесены в plan_inserts.py (задание MT, этап 3 распила scene_plan) ----
    # Секунды старта/конца (сек+кадры и легаси-кадры), прижим старта к кату, срез окна катом
    # (SNAP_TOL/_clip_end), перенос схлопнувшегося окна в новый шот, тип по файлу и стиль
    # фото (cam1/cam2 по активной камере). Имена после вызова — прежние: `inserts` (его
    # читают `_any_plate` ниже и звук — `has_video` в plan_audio.py) и `_clip_end` — им
    # сборка данных режет окно.
    _ins_t = plan_insert_timings(InsertTimingInputs(
        inserts=inserts, fps=_fps0, cam_change_sec=_cam_change_sec,
        active_cam_at=_active_cam_at, st=st, sv=_sv, sv_or=_sv_or, emit=emit))
    inserts, _clip_end = _ins_t.inserts, _ins_t.clip_end
    hl_raw = set(int(x) for x in (highlights or []) if 0 <= int(x) < len(subs))
    brk_raw = set(int(x) for x in (hl_breaks or []) if 0 <= int(x) < len(subs))
    cnt_raw = set(int(x) for x in (hl_count or []) if 0 <= int(x) < len(subs))
    joins_raw = set(int(x) for x in (hl_joins or []) if 0 <= int(x) < len(subs))
    joins_raw = joins_raw - brk_raw
    sub_words_per_row = max(1, int(_sv_or(st, "sub_words_per_row")))
    sub_rows_max = max(1, int(_sv_or(st, "sub_rows_max")))
    # Задание ZL: интро собирается и в режиме строк. Запрет из задания CH («со строками эта
    # связь ещё не продумана») снят: связь как раз прямая — слова интро вынимаются из subs
    # НИЖЕ и раньше, чем строятся строки (raw_lines), поэтому строки собираются из оставшихся
    # слов, а интро от режима субтитров не зависит.
    if word_timings is None and xml_path:
        from core.fileio import json_load_soft
        _w_sidecar = os.path.splitext(xml_path)[0] + ".words.json"
        if os.path.isfile(_w_sidecar):
            word_timings = json_load_soft(_w_sidecar)
    eff_intro = intro or []
    eff_intro_remove = intro_remove or []
    eff_intro_splits = intro_splits or []
    remove = set(int(i) for i in eff_intro_remove if 0 <= int(i) < len(subs))
    censor_source = list(subs)                    # цензор считаем по ВСЕМ словам (интро-слова звучат)
    if remove:                                   # слова интро убираем из титров, хайлайты переиндексируем
        keep = [k for k in range(len(subs)) if k not in remove]
        remap = {old: new for new, old in enumerate(keep)}
        if word_timings is not None:
            if isinstance(word_timings, list) and len(word_timings) == len(subs):
                word_timings = [word_timings[k] for k in keep]
            elif isinstance(word_timings, dict) and isinstance(word_timings.get("words"), list) and len(word_timings["words"]) == len(subs):
                word_timings = dict(word_timings, words=[word_timings["words"][k] for k in keep])
        subs = [subs[k] for k in keep]
        hl = set(remap[k] for k in hl_raw if k in remap)
        brk = set(remap[k] for k in brk_raw if k in remap)
        cnt = set(remap[k] for k in cnt_raw if k in remap)
        joins = set(remap[k] for k in joins_raw if k in remap)
    else:
        hl = hl_raw
        brk = brk_raw
        cnt = cnt_raw
        joins = joins_raw
    rows, gend = _stack_layout(subs, hl, brk, joins)
    base = base or _project_base(xml_path)
    from core import assets as _assets
    # assets live next to the XML's project OR in assets/ next to the Reelsi install.
    # This matters when the edited sequence is exported to some other folder.
    asset_base = base
    if not os.path.isfile(os.path.join(base, "assets", "assets.json")):
        alt = os.path.dirname(HERE)
        if os.path.isfile(os.path.join(alt, "assets", "assets.json")):
            asset_base = alt
    aset = _assets.resolver(asset_base)
    font_ps = _sv_or(st, "font")  # st уже резолвнут выше
    hl_font_ps = st.get("hl_font") or font_ps
    intro_font_ps = st.get("intro_font") or font_ps        # шрифты интро: пусто = как субтитры
    intro_hl_font_ps = st.get("intro_hl_font") or hl_font_ps
    # Акцентный шрифт строк интро (задание R): PostScript-имя; пусто = выключено.
    # Значение живой в стиле, в шаблон и план едет через данные строки (accent_font).
    accent_font_ps = (_sv_or(st, "accent_font")).strip()
    accent_case = (_sv_or(st, "accent_case")).strip()
    back_font_ps = (_sv_or(st, "back_font")).strip()
    back_case = (_sv_or(st, "back_case")).strip()
    # Цвета и тень текста интро (новые ключи стиля): третий цвет строки (color=="accent"),
    # свой цвет обычного/выделенного текста интро (intro_fill/intro_hl_fill, None = как
    # сегодня) и пресет тени на КАЖДОМ слове интро (intro_shadow). Дефолты не меняют .jsx
    # ни на байт (golden) — см. _intro_fill_pick/_accent_color_used/_custom_color_used ниже.
    hl_fill3 = st.get("hl_fill3")
    intro_fill = st.get("intro_fill")
    intro_hl_fill = st.get("intro_hl_fill")
    intro_shadow_on = bool(st.get("intro_shadow"))
    intro_shadow_op = float(_sv(st, "intro_shadow_op"))
    intro_shadow_dir = float(_sv(st, "intro_shadow_dir"))
    intro_shadow_dist = float(_sv(st, "intro_shadow_dist"))
    intro_shadow_soft = float(_sv(st, "intro_shadow_soft"))
    back_shadow_op = float(_sv(st, "back_shadow_op"))
    back_shadow_soft = float(_sv(st, "back_shadow_soft"))
    # Тень ПРЕКОМПА интро (задание B): у камеры 1 и камеры 2 свои цвет/непрозрачность
    # (ключи стиля intro_comp_shadow*). Дефолты — прежняя белая тень dropShadow(iL, 68):
    # при всех четырёх дефолтах .jsx остаётся прежним байт в байт (golden).
    intro_comp_shadow_fill = [float(v) for v in (_sv_or(st, "intro_comp_shadow_fill"))]
    intro_comp_shadow_op = float(
        _sv(st, "intro_comp_shadow_op"))
    intro_comp_shadow2_fill = [float(v) for v in (_sv_or(st, "intro_comp_shadow2_fill"))]
    intro_comp_shadow2_op = float(
        _sv(st, "intro_comp_shadow2_op"))
    back_step = float(_sv(st, "back_step"))
    # Шаг ПОСЛЕ блока заднего плана (задание ZZ): своя ручка, ключа в стиле может не быть
    # вовсе — тогда None, и раскладка берёт back_step (старые стили прежние байт в байт).
    # Форма с `is not None`: ноль — ЗАДАННОЕ значение, как у прочих чтений через _sv.
    _back_step_after = _sv(st, "back_step_after")
    back_step_after = None if _back_step_after is None else float(_back_step_after)
    back_scale = float(_sv(st, "back_scale"))
    # Межстрочный интервал интро (задание ZO): ОДИН множитель k на оба места — шаги строк
    # и центровку блока в Python (intro_line_ys, _intro_i_dy) и var LINE_STEP в шаблоне.
    # 100 = прежние 160 px: подстановка печатает ровно «160», .jsx прежний (golden).
    _line_step_k = float(_sv(st, "intro_line_step")) / 100.0
    # Межстрочный СТОПКИ группы с большой строкой (доработка ZY-2): свой множитель шага,
    # % от тех же 160 px. От общей ручки не зависит: у эталона владельца стопка плотнее,
    # а общий межстрочный двигает остальные группы.
    _big_step_k = float(_sv(st, "intro_big_step")) / 100.0
    # Фейд-аут прекомпа интро (задание IK): единый ключ стиля intro_fade (дефолт 0.35).
    intro_fade = float(_sv(st, "intro_fade"))
    # Интро гаснет к субтитру, если стоит на его месте (задание MH): галка и длительность
    # этого затухания. Дефолт галки True — так собраны ролики владельца (69 из 76 групп
    # гаснут ровно в момент появления следующего субтитра). Галка снята или полосы
    # субтитров блок не касается — окно группы прежнее, .jsx прежний байт в байт (golden).
    intro_sub_cut = bool(_sv(st, "intro_sub_cut"))
    intro_sub_fade = float(_sv(st, "intro_sub_fade"))
    intro_fx_hold_add = float(_sv(st, "intro_fx_hold_add"))
    # Размытие на старте и хвостовой дисклеймер (задание S): дефолты = выключено,
    # при них плейсхолдеры шаблона пусты и .jsx не меняется ни на байт (golden).
    start_blur = float(_sv_or(st, "start_blur"))
    start_blur_dur = float(_sv_or(st, "start_blur_dur"))
    disc_end_on = bool(st.get("disclaimer_end")) and bool(disclaimer)
    # Макет спикера (задание Q): точка покоя вставок Кам2 и сдвиг интро по X. Дефолты =
    # сегодняшнее поведение (0.5/0.172, 0), при них плейсхолдеры шаблона пусты и .jsx не
    # меняется ни на байт (golden). Точка наезда Камеры 1, сдвиг кадра (pan) и поворот
    # камеры читаются в plan_camera.py (задание MW) — там же и их подстановки.
    ins_c2x = float(_sv(st, "insert_c2_x"))
    ins_c2y = float(_sv(st, "insert_c2_y"))
    # Общий сдвиг точки покоя вставок кам1 (задание CB), px. Дефолт 0/0 = как сегодня.
    ins_c1x = float(_sv_or(st, "insert_c1_x"))
    ins_c1y = float(_sv_or(st, "insert_c1_y"))
    # Подложка фото-вставок (задание ZK): картинку задаёт СТИЛЬ, а решение «эта вставка
    # на подложке» — галка у самой вставки (поле plate). Файла в стиле нет — подложки нет
    # ни у кого: подстановки шаблона пустые и .jsx побайтово прежний (golden).
    _plate_path = str(_sv_or(st, "insert_plate_file") or "").strip()
    _plate_scale = float(_sv_or(st, "insert_plate_scale")) or 100.0
    _any_plate = bool(_plate_path) and any(x.get("plate") for x in inserts)
    # Цвет камер через Lumetri (задание ZJ): девять значений стиля одной дверью — их
    # читают и .jsx (LUMETRI), и превью (plan["lumetri"]), второй копии нет. Экспозиция
    # клипа (kwarg exposure с шага AE) ПРИБАВЛЯЕТСЯ к стилевой: раньше её нёс EXPOSURE
    # ровно на тех же слоях. Выключенная галка — None: подстановки шаблона прежние, .jsx
    # побайтово как раньше (golden). Ключи читаются ЯВНО (не склейкой "lm_"+k): сторож
    # схемы (test_r11_li_every_knob) ищет ручку в коде по её имени.
    lumetri = None
    if _sv(st, "lm_on"):
        lumetri = {
            "exposure": float(_sv(st, "lm_exposure")) + float(exposure or 0),
            "contrast": float(_sv(st, "lm_contrast")),
            "highlights": float(_sv(st, "lm_highlights")),
            "shadows": float(_sv(st, "lm_shadows")),
            "whites": float(_sv(st, "lm_whites")),
            "blacks": float(_sv(st, "lm_blacks")),
            "temp": float(_sv(st, "lm_temp")),
            "tint": float(_sv(st, "lm_tint")),
            "sat": float(_sv(st, "lm_sat")),
        }
    intro_x_px = float(_sv_or(st, "intro_x"))
    hl_fill = st.get("hl_fill")
    # Цвет мидтонов жёлтой строки — тот самый, что уезжает в подстановку _yellow_expr:
    # своя подстановка intro_hl_fill перебивает hl_fill. Яркость у него ОДНА на двоих
    # (задания ZN и MK2/MK3): по ней не ставится ни Tritone (выбеливает букву), ни Deep Glow
    # (к свечению строки добавляется второе свечение). Второй копии формулы нет — только
    # _tritone_on, читающая TRITONE_MAX_LUM. Цвета в стиле нет вовсе — _tritone_on(None)
    # повторяет подстановку _fill_js(None): тот же стоковый жёлтый styles.BASE (0.87).
    _yellow_rgb = intro_hl_fill if intro_hl_fill is not None else hl_fill
    _yellow_dark = _tritone_on(_yellow_rgb)
    # Регистр и цвет базовых субтитров (задание CO): регистр применяется в scene_plan к
    # ГОТОВОМУ тексту (и .jsx, и превью читают его — второй копии правила нет), цвет
    # уезжает в план для превью и в шаблон как параметр FILL. Дефолты upper/белый —
    # подстановки пустые, .jsx прежний (golden).
    sub_case = (_sv_or(st, "sub_case")).strip()
    sub_fill = st.get("sub_fill")
    # интро разбиваем на прекомпы по splits (индексы строк-начал новых групп). Режим строк
    # (sub_words_per_row > 1) группы не отменяет (задание ZL): строки субтитров собираются из
    # слов БЕЗ интро, поэтому раскладка субтитров на группы интро не влияет.
    _intro_lines = [x for x in eff_intro if (x.get("words") or (x.get("text") or "").strip())]
    _splits = sorted(set(int(s) for s in eff_intro_splits if 0 < int(s) < len(_intro_lines)))
    _bounds = [0] + _splits + [len(_intro_lines)]
    _intro_groups = [_intro_lines[_bounds[k]:_bounds[k + 1]] for k in range(len(_bounds) - 1)]

    # группы идут по таймингу: первая = самая ранняя (JS считает её началом ролика и держит её с 0).
    # _g_at и _grp_big_i — расчёт интро и живут в plan_intro.py (задание MS): копии здесь нет.
    _intro_groups.sort(key=_g_at)
    _any_glitch = any(x.get("anim") == "glitch" for g in _intro_groups for x in g)
    # Галка стиля «Deep Glow вместе со свечением строки» (задание MK): по умолчанию строка
    # жёлтого глитча СО СВЕЧЕНИЕМ (fx=="glow") Deep Glow не берёт — на ней уже висит Glo2
    # свечения строки, и два свечения складывались (владелец гасил плагин руками у 7 слов
    # из 10). Включённая галка — прежнее поведение, слово в слово.
    _dg_with_glow = bool(_sv(st, "intro_dg_with_glow"))
    # Яркий цвет жёлтого (доработка MK3): Deep Glow не берёт НИ ОДНО жёлтое слово
    # глитча — ни без свечения строки, ни с ним, и галка intro_dg_with_glow его не вернёт.
    # Причина та же, что у Tritone: на ярком цвете свечение выталкивает букву в белое, а
    # плагин кладёт сверху второе свечение. Правило одно на любой яркий жёлтый — заданный
    # в стиле (hl_fill/intro_hl_fill) или стоковый: у владельца стоковый жёлтый 0.87 в
    # большинстве стилей, и исключение «только заданный цвет» оставляло правило без дела
    # («уйдёт со всех ярких — не страшно, главное, чтобы на тёмных не уходил»). Тёмный
    # цвет (ниже порога) — правило MK: слово без свечения берёт плагин, со свечением нет.
    _dg_bright = not _yellow_dark
    # Deep Glow нужен, только если его есть куда ставить: жёлтый глитч БЕЗ свечения строки
    # или включённая галка. Иначе .jsx выходит ровно как в режиме «Встроенные» — ни PEDG2,
    # ни DG_MISS (у сборки без подходящих слов плагина нет вовсе, и ругаться не на что).
    _dg_on = glitch_glow == "deepglow2" and not _dg_bright and any(
        x.get("anim") == "glitch" and x.get("color") == "yellow"
        and (_dg_with_glow or x.get("fx") != "glow")
        for g in _intro_groups for x in g
    )
    # Строки «заднего плана» в ролике (задание A1): от этого зависит и ветка раскладки
    # строк в шаблоне, и то, считает ли Python шаги по высоте букв. Акцентная строка
    # задним планом не считается — у неё свой шрифт и свой регистр (тот же приоритет,
    # что в _intro_line_js: accent перебивает back).
    _any_back = any(bool(x.get("back") and not x.get("accent")) for g in _intro_groups for x in g)
    _any_big = any(_grp_big_i(g) is not None for g in _intro_groups)
    # ---- Звук вынесен в plan_audio.py (задание MU, этап 4 распила scene_plan) ----
    # Материалы и события SFX (поп жёлтых, глитч по группам слов, whoosh и переход на
    # катах, ризер), обрезка/точка удара/громкость (<звук>_in/_out/_at/_db), музыка,
    # цензура голоса, огибающая слоёв глитча и данные звука для шаблона. Имена ниже —
    # ровно те, что читает остальной scene_plan: перенос построчный, порядок операций и
    # подстановки не менялись.
    _au = plan_audio(AudioInputs(
        intro_groups=_intro_groups, any_glitch=_any_glitch, inserts=inserts,
        subs=subs, hl=hl, cam_change_sec=_cam_change_sec, fps=_fps0,
        st=st, sv=_sv, sv_or=_sv_or, aset=aset, intro_riser=intro_riser,
        music=music, music_random=music_random, music_dir=music_dir,
        base=base, xml_path=xml_path,
        # Цензор считаем по ВСЕМ словам (censor_source): интро-слова звучат.
        censor_source=censor_source, censor_audio=censor_audio, censor_fps=meta["fps"],
        # Словарь плана: путь голоса (Камера 1) и громкость музыки знает только scene_plan.
        voice_src=(cams[0].get("path") or "") if cams else "", music_db=music_db,
        # Общее с другими блоками: точка «музыка» (этап в логе + проверка «Стоп») и лог.
        ckpt=_ckpt, emit=emit))
    riser, pop = _au.riser, _au.pop
    trans, trans_sfx = _au.trans, _au.trans_sfx
    music_path = _au.music_path
    censor_js = _au.censor_js
    audio, glitch_sfx = _au.audio, _au.glitch_sfx
    pop_place, pop_tail = _au.pop_place, _au.pop_tail
    wsfx_place, wsfx_tail = _au.wsfx_place, _au.wsfx_tail
    riser_place, riser_tail = _au.riser_place, _au.riser_tail
    trans_place, trans_tail = _au.trans_place, _au.trans_tail
    voice_db, audio_fade = _au.voice_db, _au.audio_fade
    cams_plan = []
    for ci, c in enumerate(cams):
        cams_plan.append({"ci": ci, "path": c["path"] or "", "name": c["name"],
                          "clips": [[s, e, i, o, bool(en), _r(sc)]
                                    for s, e, i, o, en, sc in c["clips"]]})
    cams_js = _jd([{"path": c["path"], "name": c["name"], "clips": c["clips"]} for c in cams_plan])
    # ---- Блок субтитров вынесен в plan_subs.py (задание MR, этап 1 распила scene_plan) ----
    # Слова -> строки -> стопка подряд жёлтых -> появление жёлтых -> данные циклов
    # SUBS/SUB_ROWS/SUB_STACK. Имена ниже — ровно те, что читает остальной код scene_plan:
    # перенос построчный, поведение и подстановки шаблона не менялись.
    _subs = plan_subs(SubsInputs(
        subs=subs, hl=hl, brk=brk, cnt=cnt, joins=joins,
        font_ps=font_ps, hl_font_ps=hl_font_ps, sub_case=sub_case,
        sub_words_per_row=sub_words_per_row, sub_rows_max=sub_rows_max,
        width=meta["w"], height=meta["h"], fps=_fps0, cams=cams,
        word_timings=word_timings, st=st,
        # Общие с другими блоками обёртки и правила остаются в build.py (задание MR).
        sv=_sv, sv_or=_sv_or, accent_word=_accent_word, parse_count=_parse_intro_count))
    subs_plan, subs_js, sub_loop = _subs.subs, _subs.subs_js, _subs.sub_loop
    hl_row_decl, hl_blur_decl = _subs.hl_row_decl, _subs.hl_blur_decl
    hl_blur_fn, hl_short_fn, hl_blur_on = _subs.hl_blur_fn, _subs.hl_short_fn, _subs.hl_blur_on
    sub_scale, _hl_dur = _subs.sub_scale, _subs.hl_dur
    _posy, _hl_step, _hl_rise = _subs.posy, _subs.hl_step, _subs.hl_rise
    _fsize, _fsize_base, _sub_step = _subs.fsize, _subs.fsize_base, _subs.sub_step
    # ---- Камера вынесена в plan_camera.py (задание MW, этап 6 распила scene_plan) ----
    # Параметры точки наезда/pan/поворота, ключи зума по режимам («заполнение кадра»
    # умножается на них РОВНО раз), holds, разметка рото и слежение за головой. Дверь
    # одна: зависимого кода между частями камеры нет, а ключи нужны всем — шаблону
    # (CAM1_SCALE), плану (предпросмотр) и автофиту интро (plan_intro.py). Имена ниже —
    # ровно те, что читает остальной scene_plan: перенос построчный, порядок операций
    # внутри камеры (ключи -> fit -> holds -> рото -> слежение) и подстановки не менялись.
    _cam = plan_camera(CameraInputs(
        cams=cams, meta=meta, fps=_fps0, subs=subs, hl=hl,
        st=st, sv=_sv, sv_or=_sv_or,
        # Ключи зума из kwarg (None — режим стиля), галка ротоскопа и путь XML
        # (рядом с ним кэш трека головы) — камера решает по ним и разметку, и слежение.
        cam1_scale=cam1_scale, roto=roto, xml_path=xml_path))
    cam1_scale, holds = _cam.cam1_scale, _cam.holds
    cam1scale_js, cam1_ease_js = _cam.cam1scale_js, _cam.cam1_ease_js
    cam1holds_js = _cam.cam1holds_js
    roto_plan, zoom_plan = _cam.roto, _cam.zoom
    cam1_cx, cam1_cy = _cam.cam1_cx, _cam.cam1_cy
    cam1_anchor = _cam.cam1_anchor
    cam1_follow_decl, cam1_follow_js = _cam.cam1_follow_decl, _cam.cam1_follow_js
    roto_pos_cc, roto_pos_mk = _cam.roto_pos_cc, _cam.roto_pos_mk
    cam1_rot_decl, cam1_rot_cam = _cam.cam1_rot_decl, _cam.cam1_rot_cam
    roto_rot_cc, roto_rot_mk = _cam.roto_rot_cc, _cam.roto_rot_mk
    _insert_anim = (_sv_or(st, "insert_anim")).strip()
    # ---- Вставки: данные плана и подстановки вынесены в plan_inserts.py (задание MT) ----
    # Окна показа (_isec/_win), подложка и «без фона», масштабы видео, готовые ключи
    # анимаций (наезд, rise, none, вылет из-за спины) и строка INSERTS. Срез окна катом —
    # то же правило, что в таймингах выше (`_clip_end` из plan_insert_timings).
    _ip = plan_inserts(InsertsInputs(
        inserts=inserts, meta=meta, fps=_fps0, clip_end=_clip_end,
        media_dims=_media_dims, plate_path=_plate_path, plate_scale=_plate_scale,
        insert_anim=_insert_anim, st=st, sv_or=_sv_or,
        ins_c1x=ins_c1x, ins_c1y=ins_c1y, ins_c2x=ins_c2x, ins_c2y=ins_c2y, emit=emit))
    inserts_plan, inserts_js = _ip.inserts, _ip.inserts_js
    _video_segs = _ip.video_segs

    # Цензура звука — окна мьюта голоса и их JS-литерал — считается в plan_audio.py
    # (задание MU): там же события звуков, с которыми она едет в план и в шаблон.
    # Разметка РОТО (`roto_plan`) — из plan_camera.py: там же правило «нужен исходник
    # камеры», по которому фрагмент не попадает в маски.

    # Новые цвета строки интро: считаем по СЫРЫМ данным групп, а не по _intro_line_js.
    # Используются, только если хоть одна строка в ЭТОЙ сборке реально просит accent/custom
    # (иначе плейсхолдеры шаблона пусты и .jsx не меняется ни на байт, golden).
    _accent_color_used = any(x.get("color") == "accent" for g in _intro_groups for x in g)
    _custom_color_used = any(x.get("color") == "custom" for g in _intro_groups for x in g)
    # Общий масштаб интро (intro_scale, задание BG): в AE он висит на нуле «интро»
    # (родителе прекомпа) и множит СМЕЩЕНИЕ ребёнка и его размер, а собственный сдвиг
    # нула (intro_y/intro_y2) не трогает. Поэтому G входит в базу (-INTRO_BASE_Y+iDy),
    # а intro_y/intro_y2 — плоским слагаемым. 100% = дефолт: y не меняется ни на сотую.
    _G = float(_sv_or(st, "intro_scale")) / 100
    # Доля ширины кадра для автофита ОТКРЕПЛЁННОГО интро (задание MI, ручка intro_fit_w).
    # Привязанное считается по константе INTRO_FIT_W: его ширину задаёт зум камеры.
    _fit_w = float(_sv_or(st, "intro_fit_w")) / 100.0
    # Потолок увеличения того же откреплённого интро (задание MO, ручка intro_fit_max):
    # без него одно короткое слово растягивалось до 667–819 % кадра. Привязанное ручку
    # не читает — там потолок задаёт ручной gs, а ширину зум камеры.
    _fit_max = float(_sv_or(st, "intro_fit_max"))
    # Открепление интро от Камеры 1 (задание ZM): галка «интро едет с камерой» снята —
    # нулы «интро» и «интро на кам2» (и затемнение под интро) НЕ привязываются к нулу
    # Камеры 1, а идут по уже существующей ветке else: позиция в координатах кадра.
    # Подстановки пустые при дефолтном True — .jsx остаётся прежним байт в байт (golden),
    # объявление INTRO_CAM появляется только при False (иначе читать нечего).
    _intro_cam = bool(_sv(st, "intro_cam"))
    _intro_cam_decl = (
        "    var INTRO_CAM=false;  // стиль «интро едет с камерой» снят: нулы интро и затемнение\n"
        "                          // стоят в координатах кадра, а не на нуле Камеры 1 (задание ZM)\n"
    ) if not _intro_cam else ""
    _intro_cam_cond = "" if _intro_cam else " && INTRO_CAM"
    _intro_cam_shade_cmt = (
        "" if _intro_cam else
        "    // галка «интро едет с камерой» снята (задание ZM): затемнение открепляется вместе\n"
        "    // с интро — та же ветка else, координаты кадра\n"
    )
    # ---- Расчёт интро вынесен в plan_intro.py (задание MS, этап 2 распила scene_plan) ----
    # Окна групп, автофит и ширина блока, безопасная зона, раскладка строк и «большое
    # слева», затухание к субтитру (задание MH), сжатие появления, камера группы, тень
    # прекомпа и подстановки шаблона. Имена ниже — ровно те, что читает остальной код
    # scene_plan: перенос построчный, поведение и подстановки не менялись.
    _intro = plan_intro(IntroInputs(
        groups=_intro_groups, cams=cams, meta=meta, fps=_fps0,
        active_cam_at=_active_cam_at, video_segs=_video_segs, subs=_subs,
        font_ps=font_ps, intro_font_ps=intro_font_ps, intro_hl_font_ps=intro_hl_font_ps,
        accent_font_ps=accent_font_ps, accent_case=accent_case,
        back_font_ps=back_font_ps, back_case=back_case,
        any_back=_any_back, any_glitch=_any_glitch,
        st=st, sv=_sv, sv_or=_sv_or,
        # Общие с другими блоками правила остаются в build.py (задание MS).
        accent_word=_accent_word, parse_count=_parse_intro_count,
        cnt_positions=_intro_cnt_positions, line_font=_intro_line_font,
        fit_ds=_intro_fit_ds, appear_dur=_intro_appear_dur, anims=INTRO_ANIMS,
        back_step=back_step, back_step_after=back_step_after, back_scale=back_scale,
        line_step_k=_line_step_k, big_step_k=_big_step_k,
        intro_fade=intro_fade, intro_fx_hold_add=intro_fx_hold_add,
        intro_sub_cut=intro_sub_cut, intro_sub_fade=intro_sub_fade,
        intro_scale_k=_G, fit_w=_fit_w, fit_max=_fit_max, intro_cam=_intro_cam,
        cam1_scale=cam1_scale, holds=holds,
        shadow_fill=intro_comp_shadow_fill, shadow_op=intro_comp_shadow_op,
        shadow2_fill=intro_comp_shadow2_fill, shadow2_op=intro_comp_shadow2_op))
    intro_plan, intro_groups_js = _intro.intro, _intro.groups_js
    intro_idy = _intro.idy
    _intro_on2, _intro_front = _intro.on2, _intro.front
    _intro_above_roto, _intro_anchor = _intro.above_roto, _intro.anchor
    _intro_ly, _intro_lx, _intro_lk = _intro.ly, _intro.lx, _intro.lk
    _intro_sq, _intro_sub_fx = _intro.sq, _intro.sub_fx
    _intro_fx_decl, _intro_fx_out = _intro.fx_decl, _intro.fx_out
    _intro_sub_fx_decl, _intro_sub_fx_out = _intro.sub_fx_decl, _intro.sub_fx_out
    _intro_sq_decl, _intro_sq_fn = _intro.sq_decl, _intro.sq_fn
    _sq_used = _intro.sq_used
    _accent_used = _intro.accent_used

    # геометрия субтитров для предпросмотра: стопка живёт в comp-координатах, и JS не должен
    # досчитывать формулы из h (posy = sub_y*h, шаг = 0.06224*h — это контракт _ae ниже)
    _posy = int(meta["h"] * float(_sv_or(st, "sub_y")))
    _hl_step = round(meta["h"] * 0.06224, 2)
    _hl_rise = round(meta["h"] * 0.06406, 2)
    # уход субтитров на вставках rise (задание DD): для каждой rise-вставки
    # субтитры скрываются [[t0,100],[t0+en,0],[t1-ex,0],[t1,100]]; окна внахлёст объединяются
    sub_hide = []
    if _insert_anim == "rise" and bool(_sv(st, "insert_sub_swap")):
        rise_windows = []
        for xi in inserts_plan:
            if (xi.get("t") or "photo") == "photo" and xi.get("style") == "cam2":
                st_t = float(xi.get("start") or 0)
                en_t = float(xi.get("end") or 0)
                if en_t > st_t:
                    rise_windows.append((st_t, en_t, bool(xi.get("noexit"))))
        if rise_windows:
            merged_wins = []
            for st_t, en_t, ne in sorted(rise_windows, key=lambda w: (w[0], w[1])):
                if not merged_wins:
                    merged_wins.append([st_t, en_t])
                else:
                    if st_t <= merged_wins[-1][1]:
                        merged_wins[-1][1] = max(merged_wins[-1][1], en_t)
                    else:
                        merged_wins.append([st_t, en_t])
            for mw_s, mw_e in merged_wins:
                mw_noexit = any(ne for st_t, en_t, ne in rise_windows
                                if abs(en_t - mw_e) < 1e-5 and mw_s <= st_t < mw_e)
                if mw_noexit:
                    en_m, _ = _ins_enter_exit(mw_s, mw_e, True, _fps0, enter=INS_RISE_ENTER, exit_=INS_EXIT)
                    sub_hide.append([_r(mw_s), 100.0])
                    sub_hide.append([_r(mw_s + en_m), 0.0])
                    sub_hide.append([_r(mw_e), 0.0])
                    sub_hide.append([_r(mw_e + 1.0 / _fps0), 100.0])
                else:
                    en_m, ex_m = _ins_enter_exit(mw_s, mw_e, False, _fps0, enter=INS_RISE_ENTER, exit_=INS_EXIT)
                    sub_hide.append([_r(mw_s), 100.0])
                    sub_hide.append([_r(mw_s + en_m), 0.0])
                    sub_hide.append([_r(max(mw_s + en_m, mw_e - ex_m)), 0.0])
                    sub_hide.append([_r(mw_e), 100.0])
    # фон (плашка) под субтитрами (задание DE)
    # фон (плашка) под субтитрами (задание DE, обновлено DL)
    sub_comp_name = f"Субтитры ({meta['name']})" if meta.get("name") else "Субтитры (текст)"
    sub_bg_on = bool(st.get("sub_bg"))
    # Тень субтитров (задание DK): при включённой плашке собственная тень текста снимается
    sub_shadow = not sub_bg_on
    sub_shadow_js = (
        '    var ds = subLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");\n'
        '    ds.property("ADBE Drop Shadow-0002").setValue(SH_OPACITY/100*255);  // Opacity (percent in UI -> 0..255)\n'
        '    ds.property("ADBE Drop Shadow-0003").setValue(SH_DIR);      // Direction\n'
        '    ds.property("ADBE Drop Shadow-0004").setValue(SH_DIST);     // Distance\n'
        '    ds.property("ADBE Drop Shadow-0005").setValue(SH_SOFT);     // Softness\n'
    ) if sub_shadow else ""
    sub_bg_js = ""
    sub_bg_plan = None
    if sub_bg_on:
        sub_bg_fill = list(_sv(st, "sub_bg_fill"))
        sub_bg_op = float(_sv(st, "sub_bg_op"))
        sub_bg_h = float(_sv(st, "sub_bg_h"))
        sub_bg_round = float(_sv(st, "sub_bg_round"))
        sub_bg_pad = float(_sv(st, "sub_bg_pad"))
        sub_bg_padmin = float(_sv(st, "sub_bg_padmin"))
        sub_bg_dy = float(_sv(st, "sub_bg_dy"))
        sub_bg_anim = float(_sv(st, "sub_bg_anim"))
        # центр = posy + (строк - 1) * sub_step / 2 - 0.27 * fsize + sub_bg_dy (задание DI)
        sub_bg_y = round(_posy + (sub_rows_max - 1) * _sub_step / 2.0 - 0.27 * _fsize + sub_bg_dy, 2)
        sub_bg_plan = {
            "fill": sub_bg_fill,
            "op": sub_bg_op,
            "h": sub_bg_h,
            "round": sub_bg_round,
            "pad": sub_bg_pad,
            "padmin": sub_bg_padmin,
            "dy": sub_bg_dy,
            "y": sub_bg_y,
            "anim": sub_bg_anim,
            "layer": sub_comp_name,
        }
        sub_bg_expr_code = _sub_bg_expr(st, sub_layer_name=sub_comp_name)
        sub_bg_hide = [[k[0], (sub_bg_op if k[1] > 0 else 0.0)] for k in sub_hide]
        sub_bg_js = (
            "\n    // ---- фон (плашка) под субтитрами (задание DE) ----\n"
            "    var bgLayer = main.layers.addShape();\n"
            '    bgLayer.name = "Фон субтитров";\n'
            '    var bgContents = bgLayer.property("ADBE Root Vectors Group");\n'
            '    var bgRect = bgContents.addProperty("ADBE Vector Shape - Rect");\n'
            f'    bgRect.property("ADBE Vector Rect Roundness").setValue({sub_bg_round:g});\n'
            f'    bgRect.property("ADBE Vector Rect Size").expression = {_jd(sub_bg_expr_code)};\n'
            '    var bgFill = bgContents.addProperty("ADBE Vector Graphic - Fill");\n'
            f'    bgFill.property("ADBE Vector Fill Color").setValue({_fill_js(sub_bg_fill)});\n'
            f'    bgLayer.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, {sub_bg_y:g}]);\n'
            f'    bgLayer.property("ADBE Transform Group").property("ADBE Opacity").setValue({sub_bg_op:g});\n'
            '    var bgDs = bgLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");\n'
            '    bgDs.property("ADBE Drop Shadow-0001").setValue([0,0,0]);\n'
            f'    bgDs.property("ADBE Drop Shadow-0002").setValue({SUB_BG_SH_OP:g});\n'
            f'    bgDs.property("ADBE Drop Shadow-0003").setValue({SUB_BG_SH_DIR:g});\n'
            f'    bgDs.property("ADBE Drop Shadow-0004").setValue({SUB_BG_SH_DIST:g});\n'
            f'    bgDs.property("ADBE Drop Shadow-0005").setValue({SUB_BG_SH_SOFT:g});\n'
            + (f'    var SUB_BG_HIDE = {_jd(sub_bg_hide)};\n'
               '    if (SUB_BG_HIDE.length){\n'
               '        applyKeyframes(bgLayer.property("ADBE Transform Group").property("ADBE Opacity"), SUB_BG_HIDE);\n'
               '    }\n' if sub_bg_hide else '')
            + '    subLayers = [bgLayer, subLayer];\n'
        )
    # верхняя строка-прогресс (задание DF)
    top_line_on = bool(st.get("top_line"))
    top_line_js = ""
    top_line_plan = None
    if top_line_on:
        top_line_y = float(_sv(st, "top_line_y"))
        top_line_w = float(_sv(st, "top_line_w"))
        top_line_th = float(_sv(st, "top_line_th"))
        top_line_from = list(_sv(st, "top_line_from"))
        top_line_to = list(_sv(st, "top_line_to"))
        top_line_track_fill = list(_sv(st, "top_line_track_fill"))
        top_line_track_op = float(_sv(st, "top_line_track_op"))
        top_line_plan = {
            "y": top_line_y,
            "w": top_line_w,
            "th": top_line_th,
            "from": top_line_from,
            "to": top_line_to,
            "track_fill": top_line_track_fill,
            "track_op": top_line_track_op,
            "dur": meta["dur"] / meta["fps"],
        }
        top_line_r = top_line_th / 2.0
        tl_w_int = max(1, int(round(top_line_w)))
        tl_th_int = max(1, int(round(top_line_th)))
        tl_mask_r = tl_th_int / 2.0
        top_line_js = (
            "\n    // ---- верхняя строка-прогресс (задание DF) ----\n"
            "    function _tlShape(x0, y0, x1, y1, r){\n"
            "        r=Math.min(r,(x1-x0)/2,(y1-y0)/2); var k=r*0.5523;\n"
            "        var sh=new Shape(); sh.closed=true;\n"
            "        sh.vertices   =[[x0+r,y0],[x1-r,y0],[x1,y0+r],[x1,y1-r],[x1-r,y1],[x0+r,y1],[x0,y1-r],[x0,y0+r]];\n"
            "        sh.inTangents =[[-k,0],[0,0],[0,-k],[0,0],[k,0],[0,0],[0,k],[0,0]];\n"
            "        sh.outTangents=[[0,0],[k,0],[0,0],[0,k],[0,0],[-k,0],[0,0],[0,-k]];\n"
            "        return sh;\n"
            "    }\n"
            "    var tlTrack = main.layers.addShape();\n"
            '    tlTrack.name = "Строка (дорожка)";\n'
            '    var tlTrackContents = tlTrack.property("ADBE Root Vectors Group");\n'
            '    var tlTrackRect = tlTrackContents.addProperty("ADBE Vector Shape - Rect");\n'
            f'    tlTrackRect.property("ADBE Vector Rect Size").setValue([{top_line_w:g}, {top_line_th:g}]);\n'
            f'    tlTrackRect.property("ADBE Vector Rect Roundness").setValue({top_line_r:g});\n'
            '    var tlTrackFill = tlTrackContents.addProperty("ADBE Vector Graphic - Fill");\n'
            f'    tlTrackFill.property("ADBE Vector Fill Color").setValue({_fill_js(top_line_track_fill)});\n'
            f'    tlTrack.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, {top_line_y:g}]);\n'
            f'    tlTrack.property("ADBE Transform Group").property("ADBE Opacity").setValue({top_line_track_op:g});\n'
            f'    var tlProg = main.layers.addSolid([1,1,1], "Строка (прогресс)", {tl_w_int}, {tl_th_int}, 1);\n'
            f'    tlProg.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, {top_line_y:g}]);\n'
            '    var tlRamp = tlProg.property("ADBE Effect Parade").addProperty("ADBE Ramp");\n'
            f'    tlRamp.property("ADBE Ramp-0001").setValue([0, {tl_th_int / 2.0:g}]);\n'
            f'    tlRamp.property("ADBE Ramp-0002").setValue({_fill_js(top_line_from)});\n'
            f'    tlRamp.property("ADBE Ramp-0003").setValue([{tl_w_int:g}, {tl_th_int / 2.0:g}]);\n'
            f'    tlRamp.property("ADBE Ramp-0004").setValue({_fill_js(top_line_to)});\n'
            '    var tlMask = tlProg.property("ADBE Mask Parade").addProperty("ADBE Mask Atom");\n'
            '    tlMask.name = "Раскрытие";\n'
            '    var tlMaskProp = tlMask.property("ADBE Mask Shape");\n'
            f'    tlMaskProp.setValueAtTime(0, _tlShape(0, 0, {tl_th_int}, {tl_th_int}, {tl_mask_r:g}));\n'
            f'    tlMaskProp.setValueAtTime(DUR, _tlShape(0, 0, {tl_w_int}, {tl_th_int}, {tl_mask_r:g}));\n'
            '    try{ tlTrack.moveToBeginning(); }catch(e){}\n'
            '    try{ tlProg.moveToBeginning(); }catch(e){}\n'
        )
    # подпись о ролике (задание DG, обновлено DL)
    caption_on = bool(st.get("caption"))
    caption_text_raw = str(caption or "").strip()
    caption_case = _sv_or(st, "caption_case")
    caption_text = caption_text_raw.upper() if caption_case == "upper" else caption_text_raw
    caption_js = ""
    caption_plan = None
    if caption_on:
        caption_font = _sv_or(st, "caption_font")
        caption_size = float(_sv(st, "caption_size"))
        caption_fill = list(_sv(st, "caption_fill"))
        caption_x = float(_sv(st, "caption_x"))
        caption_y = float(_sv(st, "caption_y"))
        caption_bg = bool(_sv(st, "caption_bg"))
        caption_bg_fill = list(_sv(st, "caption_bg_fill"))
        caption_bg_op = float(_sv(st, "caption_bg_op"))
        caption_bg_round = float(_sv(st, "caption_bg_round"))
        # множители плашки считаются от ВИДИМОГО текста (масштаб слоя всегда 100%):
        # в эталоне 181.4/105.6 по ширине и 88.2/35.5 по высоте (задание DL-хвост)
        caption_kx = float(_sv(st, "caption_kx"))
        caption_ky = float(_sv(st, "caption_ky"))
        cap_pos_expr = _caption_pos_expr(caption_x, caption_y, caption_kx)
        caption_plan = {
            "text": caption_text,
            "font": caption_font,
            "size": caption_size,
            "fill": caption_fill,
            "x": caption_x,
            "y": caption_y,
            "case": caption_case,
            "bg": caption_bg,
            "bg_fill": caption_bg_fill,
            "bg_op": caption_bg_op,
            "bg_round": caption_bg_round,
            "kx": caption_kx,
            "ky": caption_ky,
        }
        if caption_text:
            bg_block = ""
            if caption_bg:
                size_expr = _caption_bg_size_expr(caption_kx, caption_ky)
                pos_expr = _caption_bg_pos_expr()
                bg_block = (
                    '    var capBg = main.layers.addShape();\n'
                    '    capBg.name = "Подпись (фон)";\n'
                    '    var capBgContents = capBg.property("ADBE Root Vectors Group");\n'
                    '    var capBgRect = capBgContents.addProperty("ADBE Vector Shape - Rect");\n'
                    f'    capBgRect.property("ADBE Vector Rect Roundness").setValue({caption_bg_round:g});\n'
                    f'    capBgRect.property("ADBE Vector Rect Size").expression = {_jd(size_expr)};\n'
                    '    var capBgFill = capBgContents.addProperty("ADBE Vector Graphic - Fill");\n'
                    f'    capBgFill.property("ADBE Vector Fill Color").setValue({_fill_js(caption_bg_fill)});\n'
                    f'    capBg.property("ADBE Transform Group").property("ADBE Position").expression = {_jd(pos_expr)};\n'
                    f'    capBg.property("ADBE Transform Group").property("ADBE Opacity").setValue({caption_bg_op:g});\n'
                )
            caption_js = (
                "\n    // ---- подпись о ролике (задание DG, обновлено DL) ----\n"
                + bg_block
                + f'    var capLayer = main.layers.addText({_jd(caption_text)});\n'
                '    capLayer.name = "Подпись";\n'
                '    var capDoc = capLayer.property("ADBE Text Properties").property("ADBE Text Document");\n'
                f'    var capVal = capDoc.value; capVal.resetCharStyle(); capVal.resetParagraphStyle(); capVal.text = {_jd(caption_text)};\n'
                f'    try{{ setFont(capVal, {_js(caption_font)}); }}catch(e){{}}\n'
                f'    capVal.fontSize = {caption_size:g};\n'
                f'    capVal.fillColor = {_fill_js(caption_fill)};\n'
                '    capVal.applyFill = true;\n'
                # caption_x — ЛЕВЫЙ КРАЙ блока подписи. Плашка центрируется на тексте
                # (своим выражением), поэтому её левый край садится на caption_x только
                # если центр надписи = caption_x + ширина плашки / 2 — это и считает
                # выражение на Position текста. Без плашки центрировать не от чего:
                # тогда текст просто выключен влево и начинается на caption_x.
                + ('    try{ capVal.justification = ParagraphJustification.CENTER_JUSTIFY; }catch(e){}\n'
                   if caption_bg else
                   '    try{ capVal.justification = ParagraphJustification.LEFT_JUSTIFY; }catch(e){}\n')
                + '    capDoc.setValue(capVal);\n'
                + f'    capLayer.property("ADBE Transform Group").property("ADBE Position").setValue([{caption_x:g}, {caption_y:g}]);\n'
                + (f'    capLayer.property("ADBE Transform Group").property("ADBE Position").expression = {_jd(cap_pos_expr)};\n'
                   if caption_bg else '')
                + ('    try{ capBg.moveToBeginning(); }catch(e){}\n' if caption_bg else '')
                + '    try{ capLayer.moveToBeginning(); }catch(e){}\n'
            )
    # Затемнение под интро (задание IL, масштабирование KF): единственный источник чисел —
    # этот план, из него их берут и шаблон (.jsx), и предпросмотр. Выключенная галка = None:
    # подстановка в шаблоне пустая, .jsx не меняется ни на байт (golden). Координаты — в
    # системе нула «Камера 1» (та же, в которой стоит нул «интро»: [0, INTRO_Y], template.py).
    # k масштабирует пиксели под ширину композиции относительно эталона SHADE_REF_W (доля
    # кадра постоянна при любом W). _G (intro_scale / 100) — масштаб нула интро: затемнение
    # висит на нуле «Камера 1», поэтому его scale и сдвиг SHADE_DY от intro_y масштабируются
    # на _G вслед за размером и положением текста интро.
    shade_plan = None
    if bool(st.get("intro_shade")):
        k = meta["w"] / SHADE_REF_W
        intro_y = float(_sv_or(st, "intro_y"))
        shade_plan = {
            "x": _r(SHADE_X * k), "y": _r(intro_y + SHADE_DY * k * _G),
            "scale": _r(SHADE_SCALE * _G), "w": _r(SHADE_W * k), "h": _r(SHADE_H * k),
            "ox": _r(SHADE_OX * k), "oy": _r(SHADE_OY * k), "blur": _r(SHADE_BLUR * k),
            "op": float(_sv(st, "intro_shade_op")),
        }
    # JS слоя затемнения: собирается ТОЛЬКО при включённой галке — при выключенной
    # подстановка пустая, и .jsx остаётся прежним байт в байт (golden). Слой — фигура
    # (прямоугольник с чёрной заливкой) с Box Blur; числа берутся из INTRO_SHADE, то есть
    # из плана: второй копии формул нет ни в ExtendScript, ни в превью.
    _intro_shade_js = ""
    if shade_plan is not None:
        _intro_shade_js = (
            "\n    // ---- затемнение под интро (задание IL): фигура + Box Blur, числа из плана ----\n"
            "    var INTRO_SHADE=" + _jd(shade_plan) + ";\n"
            "    var shadeLayer = main.layers.addShape();\n"
            '    shadeLayer.name = "Затемнение интро";\n'
            "    shadeLayer.inPoint = 0; shadeLayer.outPoint = DUR;\n"
            '    var shadeRoot = shadeLayer.property("ADBE Root Vectors Group");\n'
            '    var shadeGrp = shadeRoot.addProperty("ADBE Vector Group");\n'
            '    var shadeCtx = shadeGrp.property("ADBE Vectors Group");\n'
            '    try{ shadeCtx.addProperty("ADBE Vector Shape - Rect").property("ADBE Vector Rect Size")'
            '.setValue([INTRO_SHADE.w, INTRO_SHADE.h]); }catch(e){}\n'
            "    // заливка через _fill_js: одна проверенная форма для всех заливок (3 компонента в AE, задание KG)\n"
            '    try{ shadeCtx.addProperty("ADBE Vector Graphic - Fill").property("ADBE Vector Fill Color")'
            '.setValue(' + _fill_js([0, 0, 0]) + '); }catch(e){}\n'
            '    try{ shadeGrp.property("ADBE Vector Transform Group").property("ADBE Vector Position")'
            '.setValue([INTRO_SHADE.ox, INTRO_SHADE.oy]); }catch(e){}\n'
            "    // обводку не добавляем: в amdi1.aep её нет\n"
            "    var shadeBlur = null;\n"
            '    try{ shadeBlur = shadeLayer.property("ADBE Effect Parade").addProperty("ADBE Box Blur2"); }catch(e){}\n'
            "    if (shadeBlur){\n"
            "        var shadeRad = false;\n"
            '        try{ shadeBlur.property("Blur Radius").setValue(INTRO_SHADE.blur); shadeRad = true; }catch(e){}\n'
            "        // запасное имя параметра радиуса: в локализованном AE «Blur Radius» не найдётся\n"
            '        if (!shadeRad){ try{ shadeBlur.property("ADBE Box Blur2-0001").setValue(INTRO_SHADE.blur); }catch(e){} }\n'
            "    }\n"
            '    try{ shadeLayer.property("ADBE Transform Group").property("ADBE Opacity")'
            '.setValue(INTRO_SHADE.op); }catch(e){}\n'
            "    // порядок как у рото: сначала parent, ПОТОМ позиция и масштаб — AE при привязке\n"
            "    // пересчитывает локальную позицию ребёнка под трансформ нула (задание BK)\n"
            + _intro_cam_shade_cmt +
            "    if (cam1null" + _intro_cam_cond + "){ shadeLayer.parent=cam1null;\n"
            '        try{ shadeLayer.property("ADBE Transform Group").property("ADBE Position")'
            '.setValue([INTRO_SHADE.x, INTRO_SHADE.y]); }catch(e){}\n'
            '        try{ shadeLayer.property("ADBE Transform Group").property("ADBE Scale")'
            '.setValue([INTRO_SHADE.scale, INTRO_SHADE.scale]); }catch(e){}\n'
            "    } else {\n"
            '        try{ shadeLayer.property("ADBE Transform Group").property("ADBE Position")'
            '.setValue([W/2+INTRO_SHADE.x, H/2+INTRO_SHADE.y]); }catch(e){}\n'
            '        try{ shadeLayer.property("ADBE Transform Group").property("ADBE Scale")'
            '.setValue([INTRO_SHADE.scale, INTRO_SHADE.scale]); }catch(e){}\n'
            "    }\n"
        )
    # Слежение за головой (`follow_keys`) и `plan["zoom"]` — из plan_camera.py: кэш
    # `<стем>.head.json` читается там же одной дверью с ключами зума (fit уже внутри них).

    plan = {
        "fps": meta["fps"], "w": meta["w"], "h": meta["h"], "name": meta["name"],
        "dur": meta["dur"] / meta["fps"],
        "cams": cams_plan,
        # Камера 1: holds = тип интерполяции каждого ключа (1=HOLD, 0=BEZIER); keys = [кадр, %], опц. mode (drift);
        # ease = [in, out] на каждый ключ (задание B); fit = 100 — заполнение кадра уже
        # в ключах (задание ZE), поле оставлено ради превью: оно множит fit на ключ;
        # cx/cy — точка наезда в долях кадра (задание Q): при наезде неподвижна она,
        # превью рисует её же как transformOrigin и центр масштабирования
        "zoom": zoom_plan,
        "intro": intro_plan,
        # затемнение под интро (задание IL): None при выключенной галке, иначе готовые
        # числа слоя-фигуры (x/y/scale/w/h/ox/oy/blur/op) — их же рисует предпросмотр
        "shade": shade_plan,
        # общий масштаб интро, в процентах как в стиле (задание BG): превью множит на него
        # положение и размер блока; поля групп (dx/dy/ds/y) читает оно же — не переименовывать
        "intro_scale": float(_sv_or(st, "intro_scale")),
        # интро едет с камерой (задание ZM): False — нулы интро и затемнение НЕ привязаны
        # к нулу Камеры 1. Числом из плана живёт предпросмотр (ipvIntroChild): при False
        # блок идёт в координатах кадра без зума/сдвига/слежения — второй копии правила нет.
        "intro_cam": _intro_cam,
        # параметры анимаций интро: превью анимирует теми же числами,
        # что AE — вторая копия не заводится.
        "intro_anims": {
            "glitch": {
                "dur": INTRO_ANIMS["glitch"]["dur"],
                "blur": INTRO_ANIMS["glitch"]["blur"],
                "end_keys": [list(k) for k in INTRO_ANIMS["glitch"]["end_keys"]],
                "op_keys": [list(k) for k in INTRO_ANIMS["glitch"]["op_keys"]],
            },
            "reveal": {
                "dur": INTRO_ANIMS["reveal"]["dur"],
                "blur": INTRO_ANIMS["reveal"]["blur"],
                "scale": INTRO_ANIMS["reveal"]["scale"],
                "scale_3d": list(INTRO_ANIMS["reveal"]["scale_3d"]),
                "shape": INTRO_ANIMS["reveal"]["shape"],
                "smoothness": INTRO_ANIMS["reveal"]["smoothness"],
                "ease": list(INTRO_ANIMS["reveal"]["ease"]),
            },
        },
        "inserts": inserts_plan,
        "layer_order": list(_sv_or(st, "layer_order")),
        # Цвет камер через Lumetri (задание ZJ): None при выключенной галке, иначе девять
        # значений стиля (exposure уже с экспозицией клипа). Их же читает превью —
        # второй копии правил нет: .jsx и предпросмотр берут один plan["lumetri"].
        "lumetri": lumetri,
        "subs": subs_plan,
        "sub_hide": sub_hide,
        # цвет базовых субтитров (задание CO): [r,g,b] 0..1, превью красит тем же,
        # что AE — вторая копия не заводится. Жёлтые по-прежнему берут hl_fill.
        "sub_fill": list(sub_fill) if sub_fill else [1, 1, 1],
        # цвет выделения субтитров (задание CV): [r,g,b] 0..1, превью красит тем же,
        # что AE — вторая копия не заводится.
        "hl_fill": list(hl_fill) if hl_fill else [1, 0.9176, 0],
        # цвета интро для предпросмотра:
        "hl_fill3": list(hl_fill3) if hl_fill3 else [0.6863, 0.1216, 0.1216],
        "intro_fill": list(intro_fill) if intro_fill else None,
        "intro_hl_fill": list(intro_hl_fill) if intro_hl_fill else None,
        "back_scale": back_scale,
        "back_step": back_step,
        # Шаг от заднего плана к обычной строке (задание ZZ) для предпросмотра: None —
        # ключа в стиле нет, раскладка взяла back_step (превью читает готовые ys).
        "back_step_after": back_step_after,
        # Разметка рото (задание MW): готовые фрагменты из plan_camera.py — маски по ним
        # делает to_ae_full, предпросмотр читает их же для полосы «здесь рото».
        "roto": roto_plan,
        # Звук плана (задание MU): словарь собирается там же, где считаются его числа —
        # plan_audio.py. Голос, музыка, окна цензуры и события SFX — из одного места,
        # второй копии у .jsx и предпросмотра нет.
        "audio": audio,
        # стопка субтитров и кегль — для отрисовки в предпросмотре (тот же источник, что _ae)
        # intro_fsize — кегль интро (до ужатия строк, доработка ZL): превью рисует им
        # интро, fsize (ужатым) — субтитры; в режиме по слову числа равны.
        "posy": _posy, "hl_step": _hl_step, "hl_rise": _hl_rise, "fsize": _fsize,
        # Анимация жёлтых в строках (задание ZU) для предпросмотра: время появления — у
        # самого слова (words[].t0), остальные числа — плоскими полями рядом с hl_rise/
        # hl_step: превью не заводит своей копии ни одного числа. hl_dur — то же 0.35 с,
        # что литералом HL_DUR в шаблоне, hl_row_anim — режим (word/row, задание ZH).
        "hl_dur": _hl_dur, "hl_row_anim": _sv(st, "hl_row_anim"),
        "hl_blur": hl_blur_on, "hl_blur_amt": float(_sv(st, "hl_blur_amt")),
        "intro_fsize": _fsize_base,
        # масштаб слоя прекомпа субтитров (задание FE): превью рисует transform: scale()
        # с origin в posy — то же число, что уходит в Scale в .jsx
        "sub_scale": sub_scale,
        # тень субтитров (задание DK): выключается при sub_bg
        "sub_shadow": sub_shadow,
        # размытие на старте (задание S): превью рисует CSS-фильтр с той же кривой;
        # 0 = выключено, план тогда несёт ноль и превью фильтр не вешает
        "start_blur": start_blur, "start_blur_dur": start_blur_dur,
        # точка покоя cam2-вставки (задание Q: уезжает в стиль insert_c2_x/y, долями кадра)
        "ins_c2x": round(meta["w"] * ins_c2x), "ins_c2y": round(meta["h"] * ins_c2y),
        # сдвиг интро по горизонтали, px (пара к intro_y)
        "intro_x": round(intro_x_px),
    }
    if sub_bg_plan:
        plan["sub_bg"] = sub_bg_plan
    if top_line_plan:
        plan["top_line"] = top_line_plan
    if caption_plan:
        plan["caption"] = caption_plan
    # ---- Подстановки шаблона интро вынесены в plan_intro_tpl.py (задание MV, этап 5) ----
    # Готовые строки JS: цвета текста, тень слов/строк и прекомпа, раскладка строк (задний
    # план, якорь «first», «большое слева»), эффекты появления (глитч/Deep Glow/Tritone/
    # свечение) и маршрутизация слоёв над видеовставкой и рото. Имена ниже — ровно те, что
    # читает остальной код scene_plan: перенос построчный, текст подстановок не менялся.
    _itpl = plan_intro_tpl(IntroTplInputs(
        groups=_intro_groups, intro=_intro,
        any_glitch=_any_glitch, any_back=_any_back, any_big=_any_big,
        accent_color_used=_accent_color_used, custom_color_used=_custom_color_used,
        intro_fill=intro_fill, intro_hl_fill=intro_hl_fill, hl_fill3=hl_fill3,
        yellow_dark=_yellow_dark, dg_on=_dg_on, dg_with_glow=_dg_with_glow,
        shadow_on=intro_shadow_on, shadow_op=intro_shadow_op, shadow_dir=intro_shadow_dir,
        shadow_dist=intro_shadow_dist, shadow_soft=intro_shadow_soft,
        back_shadow_op=back_shadow_op, back_shadow_soft=back_shadow_soft,
        comp_shadow_fill=intro_comp_shadow_fill, comp_shadow_op=intro_comp_shadow_op,
        comp_shadow2_fill=intro_comp_shadow2_fill, comp_shadow2_op=intro_comp_shadow2_op,
        back_step=back_step, back_scale=back_scale,
        # Таблицы и правило счётчика остаются в build.py (задание MV): второй копии нет.
        anims=INTRO_ANIMS, deep_glow=DEEP_GLOW2_GLITCH,
        has_valid_count=_has_valid_count))
    _hlfill3_decl, _intro_fill_decl = _itpl.hlfill3_decl, _itpl.fill_decl
    _fill_params, _fill_call = _itpl.fill_params, _itpl.fill_call
    _intro_fill_pick = _itpl.fill_pick
    _intro_shadow_decl, _intro_word_shadow_fn = _itpl.shadow_decl, _itpl.word_shadow_fn
    _intro_word_shadow_line = _itpl.word_shadow_line
    _intro_word_shadow_word = _itpl.word_shadow_word
    _intro_ly_decl, _intro_lx_decl = _itpl.ly_decl, _itpl.lx_decl
    _intro_big_fn, _intro_big_qi_vars = _itpl.big_fn, _itpl.big_qi_vars
    _intro_big_line_pos, _intro_big_word_x = _itpl.big_line_pos, _itpl.big_word_x
    _intro_line_layout = _itpl.line_layout
    _intro_back_scale_fn, _intro_back_scale_line = _itpl.back_scale_fn, _itpl.back_scale_line
    _intro_back_scale_line_w = _itpl.back_scale_line_w
    _intro_back_scale_tmp = _itpl.back_scale_tmp
    _intro_back_scale_word, _intro_back_scale_wpx = _itpl.back_scale_word, _itpl.back_scale_wpx
    _intro_front_decl, _intro_front_arr_decl = _itpl.front_decl, _itpl.front_arr_decl
    _intro_front_route, _intro_front_raise = _itpl.front_route, _itpl.front_raise
    _intro_above_roto_decl = _itpl.above_roto_decl
    _intro_above_roto_arr_decl = _itpl.above_roto_arr_decl
    _intro_above_roto_route, _intro_above_roto_raise = \
        _itpl.above_roto_route, _itpl.above_roto_raise
    _intro_anim_fx_fn = _itpl.anim_fx_fn
    _intro_line_anim, _intro_word_anim = _itpl.line_anim, _itpl.word_anim
    _intro_hl_glow_fn = _itpl.hl_glow_fn
    _intro_group_flags, _intro_comp_glow = _itpl.group_flags, _itpl.comp_glow
    _intro_comp_shadow, _intro_comp_shadow_fn = _itpl.comp_shadow, _itpl.comp_shadow_fn
    # Дисклеймер подстраивается под шрифт (задание E). Кегль: база int(H·0.0245), но самая
    # длинная строка не должна вылезать за DISC_FIT_W ширины кадра — у SF Pro Condensed при
    # 47 она давала ровно 0.992·W, у Oswald-Bold 1194 px (за краем кадра 1080) и пользователь
    # ужимал слой руками. Кегль только УМЕНЬШАЕТСЯ: узкий шрифт дисклеймер не раздувает.
    # Ширины нет (шрифта/глифа нет в системе) — прежняя база, как сегодня.
    _disc_base = int(meta["h"] * 0.0245)
    _disc_lines = (disclaimer or "").split("\n")
    _disc_w = [_fonts.text_width(font_ps, _ln, _disc_base) for _ln in _disc_lines]
    if any(_w is None for _w in _disc_w):
        disc_size = _disc_base
    else:
        _disc_w_max = max(_disc_w)
        disc_size = (round(_disc_base * DISC_FIT_W * meta["w"] / _disc_w_max, 2)
                     if _disc_w_max > DISC_FIT_W * meta["w"] else _disc_base)
    # Интервал дисклеймера: шаг строк = «хвост вниз верхней строки + высота букв нижней +
    # disc_gap» при УЖЕ подобранном кегле (высоты даёт fonts.ink_extent по контурам глифов).
    # Зазор не задан (None = интервал авто) или высот нет — DISC_LEAD не объявляется вовсе.
    disc_lead = None
    if st.get("disc_gap") is not None and len(_disc_lines) > 1:
        _disc_ink = [_fonts.ink_extent(font_ps, _ln, disc_size) for _ln in _disc_lines]
        if all(_x is not None for _x in _disc_ink):
            disc_lead = round(max(_disc_ink[_i][1] + _disc_ink[_i + 1][0]
                                  for _i in range(len(_disc_ink) - 1))
                              + float(st["disc_gap"]), 2)
    # Применение интервала к текстовому документу — в ОБОИХ блоках дисклеймера (головной в
    # template.py, концевой ниже). При дефолтах подстановка пустая: .jsx прежний байт в байт
    # (golden). typeof-guard: DISC_LEAD объявляется только вместе с зазором стиля.
    _disc_lead_code = ('if (typeof DISC_LEAD!=="undefined"){ try{ dd.autoLeading=false;'
                       ' dd.leading=DISC_LEAD; }catch(e){} }')
    disc_lead_decl = (", DISC_LEAD=%g" % disc_lead) if disc_lead is not None else ""
    disc_lead_js = ("" if disc_lead is None else _disc_lead_code + "\n        ")
    disc_lead_js_tail = ("" if disc_lead is None else "\n    " + _disc_lead_code)
    # Служебное для сборки: готовые токены шаблона (не входят в контракт плана).
    # Подстановки камеры (cam1_moved/anchor/rot/рото-позиции, CAM1_FOLLOW) собраны в
    # plan_camera.py — здесь они только разложены по ключам, второй копии формул нет.
    plan["_ae"] = dict(
        w=meta["w"], h=meta["h"], fps=_fps_js(meta["fps"]), dur=meta["dur"] / meta["fps"],
        name=_js(meta["name"]), cams=cams_js, subs=subs_js, cam1scale=cam1scale_js,
        cam1_ease=cam1_ease_js,
        cam1holds=cam1holds_js,
        # Слои клипа и рото кам1 заполняют кадр ровно (задание ZE): их прежний масштаб
        # переехал в ключи зума нула, иначе фит растил бы кадр вокруг СВОЕГО центра.
        cam1_fit=100.0,
        cam1_follow_decl=cam1_follow_decl,
        cam1_follow_js=cam1_follow_js,
        intro_scale=float(_sv_or(st, "intro_scale")), intro_y=float(_sv_or(st, "intro_y")),
        intro_y2=float(_sv_or(st, "intro_y2")), intro_on2=_jd(_intro_on2),
        # Открепление интро от Камеры 1 (задание ZM): объявление INTRO_CAM и добавка
        # «&& INTRO_CAM» к условию привязки. При дефолтном True обе подстановки пустые —
        # .jsx прежний байт в байт (golden).
        intro_cam_decl=_intro_cam_decl,
        intro_cam_cond=_intro_cam_cond,
        # Подъём интро над видеовставкой: все подстановки пустые, когда front выключен.
        intro_front_decl=_intro_front_decl,
        intro_front_arr_decl=_intro_front_arr_decl,
        intro_front_route=_intro_front_route,
        intro_front_raise=_intro_front_raise,
        # Подъём интро над рото по положению (задание C): подстановки непустые только
        # при галке стиля и группе в нижней половине кадра, иначе .jsx прежний (golden).
        intro_above_roto_decl=_intro_above_roto_decl,
        intro_above_roto_arr_decl=_intro_above_roto_arr_decl,
        intro_above_roto_route=_intro_above_roto_route,
        intro_above_roto_raise=_intro_above_roto_raise,
        # Y базовых линий строк интро (задание A1): непусто при строках заднего плана
        # или якоре «first», иначе пусто — .jsx прежний байт в байт (golden).
        intro_ly_decl=_intro_ly_decl,
        # Большая строка (задание ZY): массивы INTRO_LX/INTRO_LK и куски шаблона для неё.
        # Нет большой строки ни в одной группе — все подстановки пустые (golden).
        intro_lx_decl=_intro_lx_decl,
        intro_big_fn=_intro_big_fn,
        intro_big_qi_vars=_intro_big_qi_vars,
        intro_big_line_pos=_intro_big_line_pos,
        intro_big_word_x=_intro_big_word_x,
        sub_hide=_jd(sub_hide),
        sub_comp_name=_js(sub_comp_name),
        # готовые iDy каждой группы (задание Q2): шаблон больше не считает опускание
        # под INTRO_SAFE_TOP сам — берёт число, как берёт INS_C2_Y. Превью читает то же
        # из plan.intro[].y, поэтому база интро живёт в одном месте.
        intro_idy=_jd(intro_idy),
        # Длительность фейд-аута прекомпов интро (задание IK)
        intro_fade=intro_fade,
        # Межстрочный шаг строк интро в пикселях (задание ZO): 160 × intro_line_step/100.
        # При дефолтных 100% %g печатает ровно «160» — .jsx прежний байт в байт (golden).
        # Число строк и центровку блока считает Python (intro_line_ys) — второго шага нет.
        intro_line_step_px=INTRO_LINE_STEP * _line_step_k,
        # Окна фейд-аута прекомпов с глитчем (ПРАВКА 3/4): подстановки непустые только
        # при глитче в ролике, иначе .jsx прежний (golden).
        intro_fx_decl=_intro_fx_decl,
        intro_fx_out=_intro_fx_out,
        # Группы, гаснущие к появлению субтитра (задание MH): окно [начало затухания,
        # конец слоя] на группу; пусто, когда таких групп нет — .jsx прежний (golden).
        intro_sub_fx_decl=_intro_sub_fx_decl,
        intro_sub_fx_out=_intro_sub_fx_out,
        # Множители длительности появления слов (задание MH): массив INTRO_SQ и его
        # читалка introSQ. Нет сжатых слов — подстановки пусты (golden).
        intro_sq_decl=_intro_sq_decl,
        intro_sq_fn=_intro_sq_fn,
        # Затемнение под интро (задание IL): непусто только при галке стиля, иначе .jsx
        # прежний байт в байт (golden). Слой создаётся сразу после камер — значит выше
        # клипов камер, а всё добавленное позже (вставки, интро, рото, субтитры, нулы)
        # встаёт выше него; блок LAYER_ORDER группы не трогает.
        intro_shade_js=_intro_shade_js,
        # Макет спикера (задание Q): точка наезда Камеры 1 и точка покоя вставок Кам2,
        # сдвиг интро по X. Дефолты пустые подстановки — .jsx прежний (golden).
        # Камера 1: якорь и позиция нула считаются от точки наезда (cx/cy доли кадра).
        # При дефолте 0.5/0.5 это ровно то, что AE ставит сам, — кода нет вовсе.
        # Сами строки (якорь, позиции и повороты рото) собраны в plan_camera.py.
        cam1_cx=cam1_cx, cam1_cy=cam1_cy,
        cam1_anchor=cam1_anchor,
        roto_pos_cc=roto_pos_cc,
        roto_pos_mk=roto_pos_mk,
        cam1_rot_decl=cam1_rot_decl,
        cam1_rot_cam=cam1_rot_cam,
        roto_rot_cc=roto_rot_cc,
        roto_rot_mk=roto_rot_mk,
        # вставки Кам2: точка покоя по X и Y в px (в стиле insert_c2_x/y, долями кадра).
        # Дефолт 0.5/0.172 — X остаётся W/2, Y как INS_C2_Y_FR*H: объявление INS_C2_X
        # и подстановка в позицию пустые, .jsx прежний (golden).
        ins_c2x=plan["ins_c2x"], ins_c2y=plan["ins_c2y"],
        ins_c2x_decl=(", INS_C2_X=%d" % plan["ins_c2x"] if ins_c2x != 0.5 else ""),
        ins_c2x_pos=("INS_C2_X" if ins_c2x != 0.5 else "W/2"),
        # интро: сдвиг по X (px), дефолт 0 — подстановка «0» даёт прежнюю строку [0,INTRO_Y]
        intro_x_js=("%g" % intro_x_px if intro_x_px else "0"),
        intro_x_p=("+%g" % intro_x_px if intro_x_px else ""),
        music=_js(music_path) if music_path else '""', music_db=music_db,
        # Громкость голоса и микро-фейд клипов посчитаны в plan_audio.py (задание MU):
        # те же числа уехали в plan["audio"], второй копии чтения стиля нет.
        voice_db=voice_db, audio_fade=audio_fade,
        riser=_js(riser) if riser else '""',
        pop=_js(pop) if pop else '""', censor=censor_js, intro_groups=intro_groups_js,
        # Звуки с обрезкой/точкой удара/громкостью (задание AA): дефолты = прежние
        # JS-строки, .jsx не меняется (golden). При заданных ключах — готовые фрагменты.
        pop_place=pop_place, pop_tail=pop_tail,
        glitch_sfx=glitch_sfx,
        wsfx_place=wsfx_place, wsfx_tail=wsfx_tail,
        riser_place=riser_place, riser_tail=riser_tail,
        trans_place=trans_place, trans_tail=trans_tail,
        intro_font=_js(intro_font_ps), intro_hl_font=_js(intro_hl_font_ps),
        intro_mode=_js(intro_mode or "word"),
        # Акцентный шрифт интро (задание R): если ни одна строка не отмечена галкой
        # или accent_font пуст — все три подстановки пустые и .jsx прежний (golden).
        accent_params=(",af" if _accent_used else ""),
        accent_font_pick=('(af||(col=="yellow"?INTRO_HL_FONT:INTRO_FONT))' if _accent_used
                          else '(col=="yellow"?INTRO_HL_FONT:INTRO_FONT)'),
        accent_call=(",ln.accent_font" if _accent_used else ""),
        # Цвета текста интро (новые ключи стиля): hl_fill3 (color=="accent"), свой
        # intro_fill/intro_hl_fill и цвет строки color=="custom" (fill_call, ln.fill).
        # Дефолты — все подстановки пустые/прежние, .jsx не меняется ни на байт (golden).
        hlfill3_decl=_hlfill3_decl,
        intro_fill_decl=_intro_fill_decl,
        fill_params=_fill_params,
        fill_call=_fill_call,
        intro_fill_pick=_intro_fill_pick,
        # Тень на каждом слове интро (intro_shadow): выключено — пустые подстановки.
        intro_shadow_decl=_intro_shadow_decl,
        intro_word_shadow_fn=_intro_word_shadow_fn,
        intro_word_shadow_line=_intro_word_shadow_line,
        intro_word_shadow_word=_intro_word_shadow_word,
        intro_anim_fx_fn=_intro_anim_fx_fn,
        dg_on=_dg_on,
        dg_report="",
        intro_hl_glow_fn=_intro_hl_glow_fn,
        intro_group_flags=_intro_group_flags,
        intro_line_anim=_intro_line_anim,
        intro_word_anim=_intro_word_anim,
        intro_comp_glow=_intro_comp_glow,
        # Тень прекомпа интро (задание B): дефолты — ровно прежняя строка dropShadow(iL, 68)
        # и пустое объявление (golden); иначе — функция introCompShadow + вызов по камере.
        intro_comp_shadow=_intro_comp_shadow,
        intro_comp_shadow_fn=_intro_comp_shadow_fn,
        intro_line_layout=_intro_line_layout,
        intro_back_scale_fn=_intro_back_scale_fn,
        intro_back_scale_line=_intro_back_scale_line,
        intro_back_scale_line_w=_intro_back_scale_line_w,
        intro_back_scale_tmp=_intro_back_scale_tmp,
        intro_back_scale_word=_intro_back_scale_word,
        intro_back_scale_wpx=_intro_back_scale_wpx,
        intro_glow=float(_sv(st, "intro_glow")),
        exposure=float(exposure or 0), roto="[]",
        # Цвет камер через Lumetri (задание ZJ): при выключенной галке подстановки несут
        # ровно прежний текст шаблона и пустое объявление — .jsx побайтово как раньше
        # (golden). При включённой: LUMETRI + applyLumetri вместо покадровой экспозиции
        # на клипах камер и их рото-копиях (экспозиция клипа уже внутри LUMETRI.exposure).
        lumetri_decl=_lumetri_decl(lumetri),
        lumetri_cam=(LUMETRI_CAM_ON if lumetri else LUMETRI_CAM_OFF),
        lumetri_roto=(LUMETRI_ROTO_ON if lumetri else LUMETRI_ROTO_OFF),
        inserts=inserts_js, trans=_js(trans) if trans else '""',
        trans_sfx=_js(trans_sfx) if trans_sfx else '""',
        hl_rise=_hl_rise, hl_step=_hl_step, hl_dur=_hl_dur,
        hl_ease_out=HL_EASE_OUT, hl_ease_in=HL_EASE_IN,
        # Жёлтые в строке, блюр появления (задание ZH) и длительность появления короткого
        # жёлтого (задание MA): при дефолтах все подстановки пусты — .jsx прежний байт в
        # байт (golden).
        hl_row_decl=hl_row_decl, hl_blur_decl=hl_blur_decl, hl_blur_fn=hl_blur_fn,
        hl_short_fn=hl_short_fn,
        ease_default=EASE_DEFAULT,
        disclaimer=_js_multiline(disclaimer) if disclaimer else '""',
        # Кегль дисклеймера строкой: целое 47 печатается ровно «47» (было %d), ужатый под
        # ширину кадра кегль — «42.85». DISC_LEAD — только при зазоре строк в стиле.
        disc_end=disc_sec, disc_size=("%g" % disc_size),
        disc_lead_decl=disc_lead_decl, disc_lead_js=disc_lead_js,
        disc_y=int(meta["h"] * 0.764),
        # Размытие на старте (задание S): Adjustment Layer поверх всего + Gaussian Blur,
        # ключи start_blur -> 0 за start_blur_dur. Выключено (start_blur=0) — пусто.
        start_blur=start_blur,
        blur_js=("" if start_blur <= 0 else
                 "\n    // размытие на старте (задание S): Adjustment Layer поверх всего,"
                 "\n    // Gaussian Blur %(sb)g -> 0 за %(sd)g c" % {"sb": start_blur, "sd": start_blur_dur}
                 # addAdjustmentLayer в API After Effects НЕТ (есть add/addNull/addSolid/
                 # addText/addCamera/addLight/addShape) — корректирующий слой это солид с
                 # флагом adjustmentLayer. И matchName эффекта — «ADBE Gaussian Blur 2»,
                 # с пробелом: он снят с живого проекта (sample1.inspect.json). Оба промаха
                 # роняют сборку в AE, а node --check их не видит — синтаксис-то верный.
                 + "\n    var sbl=main.layers.addSolid([1,1,1], \"Размытие на старте\", W, H, 1);"
                   "\n    sbl.adjustmentLayer=true;"
                   "\n    var sbe=sbl.property(\"ADBE Effect Parade\").addProperty(\"ADBE Gaussian Blur 2\");"
                   "\n    sbe.property(\"ADBE Gaussian Blur 2-0001\").setValueAtTime(0, %(sb)g);"
                   "\n    sbe.property(\"ADBE Gaussian Blur 2-0001\").setValueAtTime(%(sd)g, 0);"
                   "\n    try{ sbl.moveToBeginning(); }catch(e){}"
                   % {"sb": start_blur, "sd": start_blur_dur}),
        # Хвостовой дисклеймер (задание S): копия головного на конец контента, держится
        # 1 с, гаснет за 0.35 — та же раскладка ключей, что у головного, со сдвигом.
        # Выключено (нет галки или текст пуст) — пусто; композиция не удлиняется.
        disc_end_js=("" if not disc_end_on else
                     "\n    // дисклеймер в конце (задание S): копия головного на конец контента"
                     "\n    var dle=main.layers.addText(DISCLAIMER);"
                     "\n    var dsp=dle.property(\"ADBE Text Properties\").property(\"ADBE Text Document\");"
                     "\n    var dd=dsp.value; dd.resetCharStyle(); dd.resetParagraphStyle(); dd.text=DISCLAIMER;"
                     "\n    try{setFont(dd, FONT);}catch(e){} dd.fontSize=DISC_SIZE; dd.fillColor=[1,1,1]; dd.applyFill=true;"
                     "\n    try{dd.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}"
                     + disc_lead_js_tail +
                     "\n    dsp.setValue(dd);"
                     "\n    dle.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([W/2, DISC_Y]);"
                     "\n    dle.inPoint=DUR;"
                     "\n    var dop=dle.property(\"ADBE Transform Group\").property(\"ADBE Opacity\");"
                     "\n    dop.setValueAtTime(DUR+DISC_END-0.35, 100); dop.setValueAtTime(DUR+DISC_END, 0);"
                     "\n    dle.outPoint=DUR+DISC_END;"
                     "\n    try{ var gg=dle.property(\"ADBE Effect Parade\").addProperty(\"ADBE Glo2\");"
                     "\n         try{gg.property(\"Glow Radius\").setValue(42);}catch(e){} }catch(e){}"
                     "\n    try{ dle.moveToBeginning(); }catch(e){}"),
        # композицию удлиняем ровно на длительность хвостового дисклеймера, иначе слой
        # окажется за краем и человек его не увидит; выключено — пустая подстановка
        comp_dur=("+%.4g" % disc_sec) if disc_end_on else "",
        fsize=_fsize,
        # Кегль текста интро в шаблоне (доработка ZL): при ужатых строках субтитров introDoc
        # обязан ставить СВОЙ кегль, а не FONT_SIZE. Кегли равны (режим по слову) —
        # подстановка ровно "FONT_SIZE", и .jsx прежний байт в байт (golden).
        intro_fsize_js=("FONT_SIZE" if _fsize_base == _fsize else str(_fsize_base)),
        posy=_posy,
        font=_js(font_ps), hl_font=_js(hl_font_ps), hlfill=_fill_js(hl_fill),
        fill=_fill_js(sub_fill if sub_fill else [1, 1, 1]),
        hl_bold=("true" if st.get("hl_bold") else "false"),
        sh_op=68, sh_dir=181, sh_dist=5, sh_soft=44,
        ins_fx=_js(_sv_or(st, "insert_fx")),
        # Задание FC: «none»-вставки без анимации и без эффектов. Подстановки при
        # дефолтах (zoom/card/white) дают ровно прежний текст шаблона — .jsx не меняется
        # (golden); при none — пусто: ни вызова insFX, ни маски, ни wiggle.
        insfx_cam1=("insFX(L,\"cam1\");" if (_sv_or(st, "insert_fx")) != "none" else ""),
        insfx_cam2=("insFX(L,\"cam2\");" if (_sv_or(st, "insert_fx")) != "none" else ""),
        ins_wiggle=(
            "try{ L.property(\"ADBE Transform Group\").property(\"ADBE Position\").expression=\"wiggle(1,15)\"; }catch(e){}  // лёгкое дрожание"
            if _insert_anim != "none" else ""),
        ins_mask=(
            "if (INS_FX!=\"white\"){                          // маска-скругление только у нового вида\n"
            "            var ph=H; try{ if(pit.width&&pit.height) ph=pit.height*(W/pit.width); }catch(e){}   // высота фото в прекомпе (тянуто под ширину)\n"
            "            var mh=ph, mw=W;\n"
            "            // квадратная карточка: режем по меньшей стороне. Ультравайд (артерия, схемы) в квадрат\n"
            "            // не лезет — теряется смысл картинки, такие оставляем целиком по ширине.\n"
            "            if (mh>0 && W/mh <= INS_MASK_SQUARE_AR){ var side=Math.min(W, mh); mw=side; mh=side; }\n"
            "            // ручная правка формы карточки (поля «Маска Ш/В» в UI, % от авто): авторасчёт\n"
            "            // квадратит всё подряд, а у половины картинок предмет в квадрат не помещается.\n"
            "            // Больше самого фото маску не растягиваем — за его краем в прекомпе пусто.\n"
            "            mw = Math.max(20, Math.min(W,  mw*(ins.mw||100)/100));\n"
            "            mh = Math.max(20, Math.min(ph, mh*(ins.mh||100)/100));\n"
            "            roundMask(L, (W-mw)/2, Math.max(0,(H-mh)/2), (W+mw)/2, Math.min(H,(H+mh)/2), INS_MASK_R); }"
            if (_sv_or(st, "insert_fx")) != "none" else ""),
        ins_c1on2_x=float(_sv_or(st, "insert_c1on2_x")),
        ins_c1on2_y=float(_sv_or(st, "insert_c1on2_y")),
        # Подложка фото-вставок (задание ZK): дефолты — ровно тот текст, что был в шаблоне,
        # поэтому без единой вставки с галкой .jsx побайтово прежний (golden). Вставка с
        # галкой переопределяет их ниже — там же и объяснение формул.
        ins_plate_decl="",
        ins_plate_layer="",
        ins_photo_pos="[W/2, H/2]",
        ins_photo_scale="[_f*100,_f*100]",
        sub_loop=sub_loop,
        sub_shadow_js=sub_shadow_js,
        sub_bg_js=sub_bg_js,
        layer_order=_jd(list(_sv_or(st, "layer_order"))),
        sub_bg_null_anchor=("    nullAnchor = bgLayer;\n" if sub_bg_on else ""),
        # Масштаб слоя прекомпа субтитров (задание FE). При 100 — пусто, .jsx прежний
        # (golden). При другом значении: якорь и позицию слоя прекомпа переносим в точку
        # строки [W/2, POSY] (иначе масштаб от центра кадра утащит строку к середине и
        # sub_y начнёт врать), Scale = sub_scale. Плашка (bgLayer) — ОТДЕЛЬНЫЙ shape-слой:
        # её прямоугольник нарисован вокруг ЛОКАЛЬНОГО (0,0), поэтому якорь — локальная
        # координата точки масштабирования [0, POSY-BG_Y] (BG_Y = исходная Position.y
        # плашки), позиция — в ту же экранную точку [W/2, POSY]. Формула экрана
        # Position+(P_local-Anchor)*Scale при s=1 даёт ровно BG_Y (ничего не сдвинулось),
        # при s<1 плашка подтягивается к строке пропорционально — как в превью.
        sub_scale_js=(
            ("\n    // масштаб субтитров (задание FE): якорь и позиция — в точку строки, "
             "иначе масштаб от центра кадра утащит строку к середине и sub_y начнёт врать\n"
             "    subLayer.property(\"ADBE Transform Group\").property(\"ADBE Anchor Point\").setValue([SW/2, POSY]);\n"
             "    subLayer.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([W/2, POSY]);\n"
             "    subLayer.property(\"ADBE Transform Group\").property(\"ADBE Scale\").setValue([SUB_SCALE,SUB_SCALE]);\n"
             "    // плашка — shape-слой: прямоугольник вокруг локального (0,0), якорь — её "
             "локальная точка масштабирования\n"
             "    try{ bgLayer.property(\"ADBE Transform Group\").property(\"ADBE Anchor Point\").setValue([0, POSY-BG_Y]); }catch(e){}\n"
             "    try{ bgLayer.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([W/2, POSY]); }catch(e){}\n"
             "    try{ bgLayer.property(\"ADBE Transform Group\").property(\"ADBE Scale\").setValue([SUB_SCALE,SUB_SCALE]); }catch(e){}"
             ).replace("SUB_SCALE", "%g" % sub_scale).replace("POSY", "%d" % _posy)
             .replace("BG_Y", "%g" % (sub_bg_y if sub_bg_on else 0.0))
            if sub_scale != 100.0 else ""),
        top_line_js=top_line_js,
        caption_js=caption_js)
    if sub_words_per_row > 1:
        plan["sub_step"] = _sub_step
    if _any_plate:
        # Подложка фото-вставок (задание ZK). Подстановки непустые ТОЛЬКО когда файл в стиле
        # задан и хоть у одной вставки есть галка — иначе .jsx побайтово прежний (golden).
        # Путь подложки уезжает в .jsx ОДИН раз (INS_PLATE в шапке), а решение «этой вставке
        # подложку» шаблон принимает по полю ins.plate: у остальных вставок прекомп, маска и
        # формулы те же, что были. Слой плашки добавляется ПЕРВЫМ (фото встанет поверх неё),
        # один импорт на весь .jsx (imp дедуплицирует). Масштаб фото и сдвиг внутри прекомпа
        # посчитал Python (_ins_plate): в .jsx едут готовые ins.ps/px/py, своих формул
        # шаблон не держит.
        plan["_ae"]["ins_plate_decl"] = (
            "    var INS_PLATE = %s;   // подложка вставок с галкой «на подложке» (задание ZK): путь или пусто\n"
            % _js(_plate_path))
        plan["_ae"]["ins_plate_layer"] = (
            "        // подложка: слой ПЕРВЫМ в прекомпе, только у вставок с галкой «на подложке»\n"
            "        if(ins.plate && INS_PLATE){ var plateItem=imp(INS_PLATE);\n"
            "            if(plateItem){ toBin(plateItem,\"Вставки\");\n"
            "                var plateL=pc.layers.add(plateItem);\n"
            "                try{ plateL.property(\"ADBE Transform Group\").property(\"ADBE Position\").setValue([W/2,H/2]);\n"
            "                    var plateW=plateItem.width; if(plateW){ var plateF=W/plateW;\n"
            "                        plateL.property(\"ADBE Transform Group\").property(\"ADBE Scale\").setValue([plateF*100,plateF*100]); } }catch(e){} } }\n"
            "        ")
        plan["_ae"]["ins_photo_pos"] = (
            "(ins.plate&&INS_PLATE)?[W/2+ins.px, H/2+ins.py]:[W/2, H/2]")
        plan["_ae"]["ins_photo_scale"] = (
            "(ins.plate&&INS_PLATE)?[(ins.ps||100),(ins.ps||100)]:[_f*100,_f*100]")
        # маска-скругление — только НЕ на подложке; у остальных вставок она остаётся
        if plan["_ae"]["ins_mask"]:
            plan["_ae"]["ins_mask"] = (
                "if (!(ins.plate && INS_PLATE)) {\n"
                + "\n".join(("    " + _ln) if _ln.strip() else _ln
                            for _ln in plan["_ae"]["ins_mask"].split("\n"))
                + "\n        }")
    return plan


def _roto_js(plan, xml_path, kw, emit, cancel):
    """Рото-маски (GPU, самый долгий этап) по разметке plan.roto. `roto` выкл -> "[]".
    Превью масок не делает — оно читает ту же разметку из плана сцены (задание C/D)."""
    if not kw.get("roto") or not plan.get("roto"):
        return "[]"
    from core import styles as _styles
    from core.umsg import umsg
    st = _styles.resolve(kw.get("style"))
    _roto = None
    try:
        from core import roto as _roto
        cams = plan["cams"]
        _plan = [p for p in plan["roto"] if cams[p["ci"]].get("path")]   # нужен исходник камеры
        if _sv(st, "roto_cam1_only"):     # рото только на кусках Камеры 1 (cam2 без рото)
            _plan = [p for p in _plan if p["ci"] == 0]
        if not _plan:
            return "[]"
        # ОБЩИЙ кэш масок (имена по хэшу камера+фрагмент+низ) — реюз между пересборками
        # и XML: тот же камера+кусок не пересчитывается заново. Overwrite исключён (имена
        # уникальны по содержимому), поэтому одна папка на весь набор.
        base = kw.get("base") or _project_base(xml_path)
        roto_dir = os.path.join(base, "roto", "_cache")
        masks_by_cam = {}                    # маски делаем из ИСХОДНИКА своей камеры
        by_cam = {}
        for p in _plan:
            by_cam.setdefault(p["ci"], []).append(p)
        emit("  · рото: {chunks} кусков по {cams} камере(ам) — самый долгий этап сборки",
             chunks=len(_plan), cams=len(by_cam))
        failures = []
        for ci, ps in by_cam.items():
            masks_by_cam[ci] = _roto.alpha_for_ranges(
                cams[ci]["path"], [(p["src_start"], p["src_end"]) for p in ps],
                os.path.join(roto_dir, "cam%d" % (ci + 1)),
                bottom_pct=float(kw.get("roto_bottom") or 0), device=kw.get("roto_device"),
                emit=emit, cancel=cancel, failures=failures)
        ents = []
        missing = []
        for p in _plan:
            ms = masks_by_cam.get(p["ci"], [])
            m = next((mm for mm in ms if abs(mm["start"] - p["src_start"]) < 0.02), None)
            if m:
                ents.append({"ci": p["ci"], "ts": p["ts"], "te": p["te"],
                             "cs": _r(p["ts"] - p["src_start"]),
                             "scale": p["scale"], "mf": _r(m.get("f") or 1),
                             "mask": m["mask"]})
            elif (p["src_end"] - p["src_start"]) >= _roto.MIN_SEG_SEC:
                missing.append(p)
        if missing:
            non_micro = [p for p in _plan if (p["src_end"] - p["src_start"]) >= _roto.MIN_SEG_SEC]
            n = len(missing)
            m = len(non_micro)
            first_err = failures[0]["error"] if failures else "маска не найдена"
            msg = (f"рото не посчитано для {n} из {m} кусков "
                   f"(первая причина: {first_err}). "
                   f"Готовые маски в кэше — собери заново, или сними галку рото в стиле")
            raise SystemExit(umsg("roto_incomplete", msg, n=n, m=m, err=first_err, error=first_err))
        return _jd(ents)
    except (Cancelled, SystemExit):
        raise                                    # «Стоп» — не «рото пропущен», SystemExit — пробрасывать
    except Exception as ex:
        msg = f"рото не удалось: {ex}. Сними галку рото в стиле или исправь причину"
        raise SystemExit(umsg("roto_failed", msg, err=str(ex), error=str(ex))) from ex
    finally:
        if _roto is not None:
            try:
                _roto.release(emit=emit)         # выгрузить RVM из VRAM после сборки
            except Exception:
                pass


def to_ae_full(xml_path, jsx_path=None, return_source=False, emit=console_emit, cancel=None,
               render_dir=None, binpfx="", comps_global=False, comp_name_out=None,
               hl_count=None, hl_joins=None, **kw):
    """Сборка .jsx. Вся арифметика — scene_plan (план сцены, задание C); здесь добавляются
    рото-маски (GPU) и рендер шаблона в файл. Остальные параметры — как в scene_plan.
    cancel — колбэк «нажали Стоп?»; проверяется между этапами и внутри рото (см. Cancelled).
    render_dir — папка вывода для безголового рендера (задание BD): задана — .jsx сам
    ставит очередь рендера, сохраняет .aep и закрывает AE; пусто — ручная сборка как раньше.
    binpfx (задание FH) — префикс бинов панели проекта при сборке набора («стем — »),
    пусто при одиночной сборке (тогда имена бинов прежние — golden).
    comps_global (задание FH) — класть главную композицию в $.global.REELSI_COMPS для
    мастер-скрипта набора; при одиночной сборке False — строка пуста (golden).
    comp_name_out (задание FJ) — список, в который кладётся ИМЯ ГЛАВНОЙ КОМПОЗИЦИИ
    (meta["name"], то самое, по которому om.file пишет .mov). Рендер ждёт файл по нему,
    а не по стему .jsx — на наборе это разные вещи (файл 01_C0233.xml → композиция C0233)."""
    emit = wrap_emit(emit)
    st_pre = _styles.resolve(kw.get("style"))
    if bool(st_pre.get("cam1_head_follow")):
        try:
            meta_pre, cams_pre, _, _ = parse_full(xml_path, ncams=kw.get("ncams"))
            if cams_pre and cams_pre[0].get("path") and os.path.isfile(cams_pre[0]["path"]):
                from core import headtrack
                ranges = headtrack.cam1_ranges(cams_pre, meta_pre.get("fps"))
                if ranges:
                    headtrack.load_or_track(xml_path, cams_pre[0]["path"], ranges, emit=emit, cancel=cancel, fps=10)
        except Cancelled:
            raise
        except Exception as ex:
            emit("слежение за головой пропущено: {err}", err=ex)
    plan = scene_plan(xml_path, emit=emit, cancel=cancel, hl_count=hl_count, hl_joins=hl_joins, **kw)
    if comp_name_out is not None:
        comp_name_out.append(plan.get("name") or os.path.splitext(os.path.basename(xml_path))[0])
    # рото могло оборваться на середине (alpha_for_ranges выходит из цикла по
    # «Стопу») — недосчитанные маски в .jsx писать нельзя
    roto_js = _roto_js(plan, xml_path, kw, emit=emit, cancel=cancel)
    if (cancel or (lambda: False))():
        raise Cancelled()
    emit("  · сборка скрипта")                       # этап для логов (как раньше)
    # aep-путь задаёт Python, а не $.fileName (задание CD): AfterFX зовётся по короткому
    # 8.3-имени .jsx, и вывод имени из $.fileName дал бы .aep с коротким именем
    aep_path = (re.sub(r"\.jsx$", ".aep", jsx_path, flags=re.I)
                if render_dir and jsx_path and not return_source else None)
    ae = dict(plan["_ae"])
    # Префикс бинов панели проекта (задание FH): при сборке набора имена бинов получают
    # приставку стема ролика, иначе в общем проекте «Вставки»/«Субтитры» всех роликов —
    # каша. При одиночной сборке префикс пуст: decl пуст и toBin зовёт bin(n) как раньше —
    # .jsx прежний (golden).
    ae["binpfx_decl"] = ("var BIN_PFX = %s;\n    " % _js(binpfx)) if binpfx else ""
    ae["bin_name"] = "BIN_PFX+n" if binpfx else "n"
    ae["dg_report"] = _dg_report(render_dir, comps_global, ae)
    jsx = AE_FULL % dict(ae, roto=roto_js,
                         tail=_render_tail(render_dir, aep_path, comps_global=comps_global),
                         imp_miss=_imp_miss(render_dir))
    if render_dir is None and not comps_global and not return_source and ae.get("dg_on"):
        jsx = _dg_wrap(jsx)
    nclips = sum(len(c["clips"]) for c in plan["cams"])
    if return_source:                          # для мультифайла «один .jsx на всё»
        return jsx, nclips, len(plan["subs"])
    jsx_path = jsx_path or (os.path.splitext(xml_path)[0] + ".jsx")
    # utf-8-sig: ExtendScript без BOM может прочитать файл в системной кодировке (cp1251).
    # Атомарно: «Стоп»/сбой между усечением и записью оставлял пустой .jsx на месте
    # собранного — AE открывал ноль клипов, а пересобрать его было уже нечем (IB, п. 2).
    atomic_text_write(jsx_path, jsx, encoding="utf-8-sig")
    if not return_source and plan.get("subs"):
        from core.subs import write_srt
        srt_path = os.path.splitext(jsx_path)[0] + ".srt"
        write_srt(plan["subs"], srt_path)
        xml_srt = os.path.splitext(xml_path)[0] + ".srt"
        if os.path.abspath(xml_srt) != os.path.abspath(srt_path):
            try:
                write_srt(plan["subs"], xml_srt)
            except Exception:
                pass
    return jsx_path, nclips, len(plan["subs"])


def write_srt_for(xml_path, srt_path=None, **kw):
    """Собрать план сцены для XML и записать .srt файл рядом с XML (или по указанному пути)."""
    srt_path = srt_path or (os.path.splitext(xml_path)[0] + ".srt")
    plan = scene_plan(xml_path, **kw)
    if plan.get("subs"):
        from core.subs import write_srt
        write_srt(plan["subs"], srt_path)
        return srt_path
    return None


def virtual_edl(xml_path, ncams=None):
    """Виртуальный EDL финального XML: что реально видно/слышно на таймлайне.
    Возвращает dict: fps,w,h,dur(сек), cams=[{name,path}],
    segs=[{ci,ts,te,src}] (видео: верхняя включённая дорожка побеждает, сек),
    audio=[{ts,te,src}] (звук ВСЕГДА с камеры 1), words=[{s,e,w}] (сек).
    Используется предпросмотром (/api/aicut_preview) и draft-рендером."""
    meta, cams, subs, _ins = parse_full(xml_path, ncams=ncams)
    fps = meta["fps"] or 60
    raw = []
    for ci, c in enumerate(cams):
        if not c.get("path"):
            continue
        for (s, e, i, o, en, *rest) in c["clips"]:
            if en and e > s:
                raw.append((s, e, ci, i))       # ci третьим — этого ждёт cover_sweep
    segs = []
    for b0, b1, k in cover_sweep(raw):          # кто виден на отрезке; topmost track wins
        s, _e, ci, i = raw[k]
        src = (i + (b0 - s)) / fps
        if segs and segs[-1]["ci"] == ci and abs(
                segs[-1]["src"] + (segs[-1]["te"] - segs[-1]["ts"]) - src) < 1e-3:
            segs[-1]["te"] = b1 / fps                       # merge contiguous same-cam
        else:
            segs.append({"ci": ci, "ts": b0 / fps, "te": b1 / fps, "src": src})
    audio = []
    if cams and cams[0].get("path"):
        for (s, e, i, o, en, *rest) in sorted(cams[0]["clips"]):
            if en and e > s:
                src = i / fps
                if audio and abs(audio[-1]["src"] + (audio[-1]["te"] - audio[-1]["ts"]) - src) < 1e-3:
                    audio[-1]["te"] = e / fps
                else:
                    audio.append({"ts": s / fps, "te": e / fps, "src": src})
    dur = (meta.get("dur") or 0) / fps or (segs[-1]["te"] if segs else 0)
    return {"fps": fps, "w": meta.get("w"), "h": meta.get("h"), "dur": dur,
            "cams": [{"name": c.get("name"), "path": c.get("path")} for c in cams],
            "segs": segs, "audio": audio,
            "words": [{"s": s / fps, "e": e / fps, "w": w} for (s, e, w) in subs]}


def _dg_report(render_dir=None, comps_global=False, ae=None):
    """Сообщение внутри таймлайна при отсутствии Deep Glow 2 (matchName PEDG2, задание HD).
    Только если в плане есть жёлтые слова глитча и выбран режим deepglow2 (ae.get('dg_on')).
    Три режима:
    - render_dir задан: _LOG('ОШИБКА: ' + ...) для слива в .aelog.txt;
    - comps_global: дозапись в $.global.REELSI_MASTER_LOG под мастером набора;
    - ручная сборка: суммирование в $.global.REELSI_DG_MISS (alert покажет _dg_wrap)."""
    if not ae or not ae.get("dg_on"):
        return ""
    msg = (
        '"Deep Glow 2 (PEDG2) не найден в After Effects: у " + DG_MISS + " слов глитча в «" '
        '+ main.name + "» нет свечения. Установите плагин или в Reelsi выберите: Настройки → Инструменты → Свечение глитча → Встроенные."'
    )
    if render_dir:
        return '    if(DG_MISS>0) _LOG("ОШИБКА: " + %s);\n' % msg
    if comps_global:
        return (
            "    if(DG_MISS>0 && $.global.REELSI_MASTER_LOG){\n"
            "        try{\n"
            "            var _dglog = new File($.global.REELSI_MASTER_LOG);\n"
            '            _dglog.encoding = "UTF-8";\n'
            '            _dglog.open("a");\n'
            '            _dglog.writeln("ОШИБКА: " + %s);\n'
            "            _dglog.close();\n"
            '        }catch(e){ _LOG("запись в REELSI_MASTER_LOG: " + e); }\n'
            "    }\n" % msg
        )
    return "    if(DG_MISS>0) $.global.REELSI_DG_MISS=($.global.REELSI_DG_MISS||0)+DG_MISS;\n"


def _dg_wrap(body):
    """Оборачивает .jsx ручной сборки в объявление и проверку счётчика DG_MISS (задание HD).
    В начало — сброс $.global.REELSI_DG_MISS=0;, в конец — alert и повторный сброс."""
    alert_msg = (
        '"Deep Glow 2 (PEDG2) не найден в After Effects: у " + $.global.REELSI_DG_MISS + '
        '" слов глитча нет свечения. Установите плагин или в Reelsi выберите: Настройки → Инструменты → Свечение глитча → Встроенные."'
    )
    head = "$.global.REELSI_DG_MISS=0;\n"
    tail = "\nif($.global.REELSI_DG_MISS){\n    alert(%s);\n}\n$.global.REELSI_DG_MISS=0;\n" % alert_msg
    return head + body + tail


def _imp_miss(render_dir=None):
    """Что делает imp() при пропавшем файле (задание BD). Ручная сборка — alert: человек
    у экрана видит, какой файл не нашёлся. Безголовый прогон (-noui) — alert это модалка,
    которую никто не закроет: процесс зависнет навсегда. Пишем в $.writeln (лог aerender /
    AfterFX) и продолжаем, вернув null — у всех вызовов imp() есть проверка `if(...)`."""
    if not render_dir:
        return 'alert("Не найден файл:\\n"+p);'
    return '$.writeln("Не найден файл: "+p);'


def _render_tail(render_dir=None, aep_path=None, comps_global=False):
    """Хвост .jsx ПОСЛЕ app.endUndoGroup(). Обычный (ручной) режим — просто открыть
    композицию и ничего не сохранять: пользователь сохраняет сам, куда хочет (задание BD).
    render_dir задан — безголовый рендер: очередь рендера ставит СКРИПТ, а не флаги
    aerender (-RStemplate/-OMtemplate; ошибка в них вылезет в середине рендера). Три
    вещи, на которых это ломается:
    - «Untitled 1» — пресет вывода пользователя, лежит ТОЛЬКО в AE 26.2; применяется
      по точной строке. Версия поэтому не подменяется, а ошибка применения оборачивается.
    - Пресет несёт СВОЙ путь вывода: om.file задаём ПОСЛЕ applyTemplate, иначе пресет
      молча перебьёт папку/имя и рендер уедет не туда.
    - Сборка живёт в памяти, а aerender работает по .aep — нужен app.project.save.
    Очередь перед добавлением чистим: иначе отрендерятся и старые элементы.
    aep_path — куда сохранять .aep (задание CD): Python и так знает целевой путь, а
    $.fileName после запуска по короткому имени .jsx вернул бы короткое имя .aep.
    Лог хвоста — в <стем>.aelog.txt рядом с проектом: в -noui наши $.writeln не видны
    вовсе (задание CD), файл же Python читает после возврата AfterFX. Создаётся ПЕРВЫМ
    делом — его отсутствие и есть признак «скрипт не запустился».
    comps_global (задание FH) — собрать композиции в $.global.REELSI_COMPS: мастер-скрипт
    набора evalFile'ит ролики и собирает их главные композиции из этого массива. При
    обычной ручной сборке (False) строка пуста — .jsx прежний (golden)."""
    if not render_dir:
        if comps_global:
            comps_push = (
                "if(!$.global.REELSI_COMPS)$.global.REELSI_COMPS=[];"
                "$.global.REELSI_COMPS.push(main);\n"
                "    if($.global.REELSI_MASTER_LOG){\n"
                "        try{\n"
                "            var _mlog = new File($.global.REELSI_MASTER_LOG);\n"
                "            _mlog.encoding = \"UTF-8\";\n"
                "            _mlog.open(\"a\");\n"
                "            _mlog.writeln(\"таймлайн ok: \" + main.name);\n"
                "            _mlog.close();\n"
                "        }catch(e){ _LOG(\"запись в REELSI_MASTER_LOG: \" + e); }\n"
                "    }\n    "
            )
            return "    %smain.openInViewer();\n    app.endUndoGroup();\n" % comps_push
        return "    main.openInViewer();\n    app.endUndoGroup();\n"
    # File в AE ест и смешанные разделители, но косые — канон; Python-путь копируется в JS-литерал
    render_dir = render_dir.replace("\\", "/").rstrip("/")
    aep = (aep_path or "").replace("\\", "/")
    aelog = re.sub(r"\.aep$", ".aelog.txt", aep)
    return ("    app.endUndoGroup();\n"
            "    // ---- безголовый рендер (задание BD): очередь + save + quit ----\n"
            "    // Лог — ФАЙЛОМ, а не $.writeln: в -noui наши $.writeln не доезжают (задание CD).\n"
            "    // Файл открываем ПЕРВЫМ делом: его отсутствие у Python = «скрипт не запустился».\n"
            "    var _log = new File(%s);\n"
            "    _log.encoding = \"UTF-8\";\n"
            "    _log.open(\"w\");\n"
            "    for (var _pi=0;_pi<_pending.length;_pi++) _log.writeln(_pending[_pi]);   // слив ранних ошибок сборки (задание CE)\n"
            "    _log.writeln(\"REELSI-TAIL: начат\");\n"
            "    try{\n"
            "        // .aep путь задаёт Python (задание CD): короткое имя .jsx не должно\n"
            "        // сдвигать имя проекта. Сохраняем ДО очереди: .aep обязан появиться,\n"
            "        // даже если очередь не собралась — иначе «не сохранил проект» не\n"
            "        // отличить от «сломалось на пресетах» (задание BT).\n"
            "        var f = new File(%s);\n"
            "        try{\n"
            "            app.project.save(f);\n"
            "            _log.writeln(\"save#1: ok\");\n"
            "        }catch(e){\n"
            "            _log.writeln(\"первый save не выполнился: \" + e);\n"
            "        }\n"
            "        var rq0 = app.project.renderQueue;\n"
            "        while (rq0.numItems > 0) rq0.item(rq0.numItems).remove();   // очередь чистим: aerender рендерит всё, что в ней\n"
            "        var rq = rq0.items.add(main);\n"
            "        try{\n"
            "            rq.applyTemplate(\"Best Settings\");\n"
            "            _log.writeln(\"applyTemplate('Best Settings'): ok\");\n"
            "        }catch(e){\n"
            "            _log.writeln(\"applyTemplate('Best Settings') не применился: \" + e);\n"
            "        }\n"
            "        var om = rq.outputModule(1);\n"
            "        try{\n"
            "            om.applyTemplate(\"Untitled 1\");\n"
            "            _log.writeln(\"applyTemplate('Untitled 1'): ok\");\n"
            "        }catch(e){\n"
            "            _log.writeln(\"applyTemplate('Untitled 1') не применился: \" + e);\n"
            "        }\n"
            "        try{\n"
            "            var _out = new File(%s + \"/\" + main.name + \".mov\");   // ПОСЛЕ applyTemplate: пресет несёт свой путь\n"
            "            if(_out.exists){ _out.remove(); _log.writeln(\"перезаписан: \" + main.name + \".mov\"); }\n"
            "            om.file = _out;\n"
            "            _log.writeln(\"om.file: ok\");\n"
            "        }catch(e){\n"
            "            _log.writeln(\"установка om.file не вышла: \" + e);\n"
            "        }\n"
            "        try{\n"
            "            app.project.save(f);   // второй раз: в .aep должна попасть очередь, иначе aerender отрендерит пустоту\n"
            "            _log.writeln(\"save#2: ok\");\n"
            "        }catch(e){\n"
            "            _log.writeln(\"второй save не выполнился: \" + e);\n"
            "        }\n"
            "        _log.writeln(\"rq.numItems=\" + rq0.numItems);\n"
            "        _log.writeln(\"exists=\" + f.exists);\n"
            "    }catch(e){\n"
            "        _log.writeln(\"непредвиденная ошибка хвоста: \" + e);\n"
            "    }finally{\n"
            "        _log.close();   // сбросить буфер ДО app.quit, иначе Python прочитает пустой файл\n"
            "        app.quit();   // в finally: иначе исключение оставит AE висеть процессом\n"
            "    }\n") % (_js(aelog), _js(aep), _js(render_dir))


def build_combined(jobs, out_jsx, emit=None, cancel=None, progress=None,
                   comps_global=False, comp_names_out=None):
    """jobs: list of dicts, each = {"xml_path": ..., **to_ae_full kwargs}. Concatenate
    every file's build script into ONE .jsx that creates several comps in one AE project.

    emit/progress/cancel — чтобы «один .jsx на всё» был виден и останавливаем так же,
    как поштучная сборка: раньше эта ветка молчала весь прогон (в логе одна строка на
    старте и одна в конце) и «Стоп» не проверяла вовсе.

    comps_global (задание C) — собрать таймлайны так, чтобы мастер-скрипт рендера мог
    выполнить этот ОДИН файл: каждый таймлайн получает comps_global=True (главная
    композиция кладётся в $.global.REELSI_COMPS — из чего мастер строит очередь) и
    binpfx="<стем> — " (иначе бины «Вставки»/«Субтитры» всех роликов в одном проекте —
    каша). False по умолчанию: кнопка «Собрать набор» собирает .jsx прежним (golden).
    comp_names_out (задание C) — список, в который складываются ИМЕНА главных
    композиций по порядку таймлайнов (как в to_ae_full через свой comp_name_out: имя
    композиции ≠ стем файла, а .mov рендер ждёт именно по имени)."""
    emit = wrap_emit(emit)
    cancel = cancel or (lambda: False)
    parts = []
    for i, j in enumerate(jobs, 1):
        if cancel():
            raise Cancelled()
        stem = os.path.splitext(os.path.basename(j["xml_path"]))[0]
        emit("[{cur}/{total}] {stem} — сборка таймлайна…", cur=i, total=len(jobs), stem=stem)
        if progress:
            progress(i, len(jobs))
        kw = {k: v for k, v in j.items() if k != "xml_path"}
        kw.setdefault("emit", emit)
        kw["cancel"] = cancel
        if comps_global:
            kw["binpfx"] = stem + " — "
            kw["comps_global"] = True
        if comp_names_out is not None:
            kw["comp_name_out"] = comp_names_out
        src, nc, ns = to_ae_full(j["xml_path"], return_source=True, **kw)
        if j.get("xml_path"):
            # .srt рядом с XML пишет отдельный scene_plan: to_ae_full при
            # return_source=True его не пишет. Без фильтра сюда же уехали бы
            # binpfx/comps_global/comp_name_out — их scene_plan не принимает.
            plan_j = scene_plan(j["xml_path"], **{k: v for k, v in kw.items()
                                                  if k not in ("binpfx", "comps_global",
                                                               "comp_name_out")})
            if plan_j.get("subs"):
                from core.subs import write_srt
                write_srt(plan_j["subs"], os.path.splitext(j["xml_path"])[0] + ".srt")
        emit("  готово: {clips} клипов, {subs} субтитров", clips=nc, subs=ns)
        parts.append(src)
    body = "\n\n// ===== следующий таймлайн =====\n\n".join(parts)
    if not comps_global and "REELSI_DG_MISS" in body:
        body = _dg_wrap(body)
    atomic_text_write(out_jsx, body, encoding="utf-8-sig")
    return out_jsx, len(parts)


def _write_master(jsx_list, master_path, aep_path, render_dir):
    """Мастер-скрипт набора: лог первым делом, evalFile каждого ролика в своём try/catch,
    очередь из всех композиций, save ДО и ПОСЛЕ, quit в finally."""
    render_dir = render_dir.replace("\\", "/").rstrip("/")
    aep = aep_path.replace("\\", "/")
    aelog = re.sub(r"\.aep$", ".aelog.txt", aep)
    files = ",\n        ".join(_js(p.replace("\\", "/")) for p in jsx_list)
    jsx = (
        "// Reelsi -> мастер набора (задание FH): N роликов в ОДИН проект AE, один aerender.\n"
        "// Лог — файлом ПЕРВЫМ делом: его отсутствие у Python = «скрипт не запустился».\n"
        "$.global.REELSI_MASTER_LOG = %s;\n"
        "var _log = new File($.global.REELSI_MASTER_LOG);\n"
        "    _log.encoding = \"UTF-8\";\n"
        "    _log.open(\"w\");\n"
        "    _log.writeln(\"REELSI-MASTER: начат\");\n"
        "try{\n"
        "    $.global.REELSI_COMPS = [];\n"
        "    var _files = [\n"
        "        %s\n"
        "    ];\n"
        "    // 1) каждый ролик — evalFile в СВОЁМ try/catch: упавший не мешает остальным.\n"
        "    //    ExtendScript буферизует файл-лог до close(), поэтому лог мастера ЗАКРЫВАЕМ\n"
        "    //    перед каждым $.evalFile (чтобы дочерний скрипт мог дописать «таймлайн ok:»)\n"
        "    //    и открываем заново на дозапись ПОСЛЕ: два открытых дескриптора одного файла\n"
        "    //    затрут строки друг друга (задания FJ, GN).\n"
        "    for (var _fi=0; _fi<_files.length; _fi++){\n"
        "        _log.close();\n"
        "        var _evalErr = null;\n"
        "        try{\n"
        "            $.evalFile(new File(_files[_fi]));\n"
        "        }catch(e){\n"
        "            _evalErr = e;\n"
        "        }\n"
        "        _log = new File($.global.REELSI_MASTER_LOG);\n"
        "        _log.encoding = \"UTF-8\";\n"
        "        _log.open(\"a\");\n"
        "        if (_evalErr){\n"
        "            _log.writeln(\"evalFile ОШИБКА: \" + _files[_fi] + \" — \" + _evalErr);\n"
        "        } else {\n"
        "            _log.writeln(\"evalFile ok: \" + _files[_fi]);\n"
        "        }\n"
        "        _log.close();\n"
        "        _log = new File($.global.REELSI_MASTER_LOG);\n"
        "        _log.encoding = \"UTF-8\";\n"
        "        _log.open(\"a\");\n"
        "    }\n"
        "    // 2) очередь: чистим (aerender рендерит всё, что в ней), save ДО очереди —\n"
        "    // .aep обязан появиться, даже если очередь не собралась\n"
        "    var rq0 = app.project.renderQueue;\n"
        "    while (rq0.numItems > 0) rq0.item(rq0.numItems).remove();\n"
        "    var f = new File(%s);\n"
        "    try{\n"
        "        app.project.save(f);\n"
        "        _log.writeln(\"save#1: ok\");\n"
        "    }catch(e){\n"
        "        _log.writeln(\"первый save не выполнился: \" + e);\n"
        "    }\n"
        "    // 3) все собранные композиции — в очередь, каждой Best Settings + Untitled 1;\n"
        "    // om.file ПОСЛЕ applyTemplate: пресет несёт свой путь и молча перебьёт папку\n"
        "    for (var _ci=0; _ci<$.global.REELSI_COMPS.length; _ci++){\n"
        "        var _c = $.global.REELSI_COMPS[_ci];\n"
        "        try{\n"
        "            var rq = rq0.items.add(_c);\n"
        "            rq.applyTemplate(\"Best Settings\");\n"
        "            var om = rq.outputModule(1);\n"
        "            om.applyTemplate(\"Untitled 1\");\n"
        "            var _out = new File(%s + \"/\" + _c.name + \".mov\");\n"
        "            if(_out.exists){ _out.remove(); _log.writeln(\"перезаписан: \" + _c.name + \".mov\"); }\n"
        "            om.file = _out;   // ПОСЛЕ applyTemplate: пресет несёт свой путь\n"
        "            _log.writeln(\"comp ok: \" + _c.name);\n"
        "        }catch(e){\n"
        "            _log.writeln(\"композиция «\" + _c.name + \"» не встала в очередь: \" + e);\n"
        "        }\n"
        "        // тот же приём: сбросить буфер — по comp ok видно, что сборка кончилась\n"
        "        _log.close();\n"
        "        _log = new File($.global.REELSI_MASTER_LOG);\n"
        "        _log.encoding = \"UTF-8\";\n"
        "        _log.open(\"a\");\n"
        "    }\n"
        "    // 4) save ПОСЛЕ очереди: в .aep должна попасть очередь, иначе aerender отрендерит пустоту\n"
        "    try{\n"
        "        app.project.save(f);\n"
        "        _log.writeln(\"save#2: ok\");\n"
        "    }catch(e){\n"
        "        _log.writeln(\"второй save не выполнился: \" + e);\n"
        "    }\n"
        "    _log.writeln(\"rq.numItems=\" + rq0.numItems);\n"
        "    _log.writeln(\"exists=\" + f.exists);\n"
        "}catch(e){\n"
        "    _log.writeln(\"непредвиденная ошибка мастера: \" + e);\n"
        "}finally{\n"
        "    try{ _log.close(); }catch(e){}\n"
        "    $.global.REELSI_MASTER_LOG = null;\n"
        "    app.quit();\n"
        "}\n" % (_js(aelog), files, _js(aep), _js(render_dir))
    )
    atomic_text_write(master_path, jsx, encoding="utf-8-sig")
    return master_path