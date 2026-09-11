# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Тесты проверялок .jsx и проекта AE (`verify_jsx.py`, `verify_ae.py`).

Смысл этих тестов — не «верификатор запускается», а «верификатор ЛОВИТ». На семи
боевых .jsx он молчит; молчащая проверка полезна ровно настолько, насколько
доказано, что она умеет кричать. Поэтому почти каждый тест берёт заведомо
исправный .jsx и ломает в нём РОВНО ОДНУ вещь — ту, что когда-то стоила
реального разбора в AE (см. «Подводные камни» в ARCHITECTURE.md).

Запуск: python -m pytest reelsi/tests -q
"""
import copy
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
# verify_ae.py — скрипт в tools/ (задание GU): он не пакет, поэтому путь к папке
sys.path.insert(0, os.path.join(ROOT, "tools"))

from core import verify_jsx  # noqa: E402
import verify_ae  # noqa: E402


# --------------------------------------------------------------- заготовки

def _media(tmp_path, name):
    """Настоящий файл на диске — проверка «файла нет» не должна мешать другим."""
    p = tmp_path / name
    p.write_bytes(b"\x00")
    return str(p).replace("\\", "\\\\")


@pytest.fixture()
def parts(tmp_path):
    """Исправный набор структур. Тест портит одну и собирает .jsx."""
    cam1 = _media(tmp_path, "cam1.mov")
    cam2 = _media(tmp_path, "cam2.mov")
    mask = _media(tmp_path, "mask.mp4")
    photo = _media(tmp_path, "photo.png")
    video = _media(tmp_path, "clip.mp4")
    return {
        "CAM": [
            {"path": cam1, "name": "камера1", "clips": [[0, 60, 0, 60, True, 100],
                                                        [60, 120, 60, 120, True, 100]]},
            {"path": cam2, "name": "Камера2", "clips": [[120, 180, 0, 60, True, 100]]},
        ],
        "SUBS": [[0, 10, "ПЕРВОЕ", 0, 0, 10], [11, 20, "ВТОРОЕ", 1, 0, 20]],
        "ROTO": [{"ci": 0, "ts": 0.0, "te": 2.0, "cs": 0.0, "scale": 50, "mf": 2, "mask": mask}],
        "INTRO_GROUPS": [[{"color": "white", "words": ["ЕСЛИ", "ВЫ"], "times": [0.1, 0.3]}]],
        "INSERTS": [{"t": "photo", "style": "cam1", "media": photo, "start": 1.0, "end": 3.0},
                    {"t": "video", "style": "cam2", "media": video, "start": 4.0, "end": 6.0}],
        "CAM1_SCALE": [[0, 182], [62, 100]],
    }


def write_jsx(tmp_path, parts, name="build.jsx"):
    lines = ["// Reelsi -> After Effects FULL build (auto-generated)"]
    for key in verify_jsx.WANTED:
        lines.append("    var %s=%s;" % (key, json.dumps(parts[key], ensure_ascii=True,
                                                         separators=(",", ":"))))
    lines.append("app.beginUndoGroup('Reelsi');")
    p = tmp_path / name
    p.write_text("\n".join(lines), encoding="utf-8-sig")
    return str(p)


def errors_of(path, **kw):
    return verify_jsx.verify(path, **kw).errors


def has(errs, needle):
    return any(needle in e for e in errs)


# --------------------------------------------------------------- база

def test_valid_jsx_is_clean(tmp_path, parts):
    rep = verify_jsx.verify(write_jsx(tmp_path, parts))
    assert rep.ok, rep.errors
    # заодно проверим, что структуры реально извлеклись, а не «нет ошибок, потому что нечего проверять»
    assert any("SUBS: 2 слов" in m for m in rep.info)


def test_structs_extracted_from_real_jsx(tmp_path, parts):
    raw = open(write_jsx(tmp_path, parts), encoding="utf-8-sig").read()
    got = verify_jsx.extract_structs(raw)
    assert set(got) == set(verify_jsx.WANTED)
    assert got["CAM"][0]["clips"][0][1] == 60


def test_missing_struct_is_error(tmp_path, parts):
    """`var SUBS` пропал целиком — .jsx синтаксически цел, но сломан по смыслу."""
    raw = open(write_jsx(tmp_path, parts), encoding="utf-8-sig").read()
    raw = "\n".join(l for l in raw.splitlines() if "var SUBS=" not in l)
    p = tmp_path / "nosubs.jsx"
    p.write_text(raw, encoding="utf-8-sig")
    assert has(errors_of(str(p)), "нет структуры SUBS")


def test_all_timelines_are_checked_not_just_the_first(tmp_path, parts):
    """Склейка «один .jsx на всё» — N таймлайнов подряд. Разбирали первое вхождение
    `var CAM=`, то есть проверяли только первый, а остальные ехали в AE вслепую
    (2026-08-01: AV1-вставка во ВТОРОМ таймлайне Reelsi_all.jsx — тут чисто, AE лёг)."""
    good = open(write_jsx(tmp_path, parts, "one.jsx"), encoding="utf-8-sig").read()
    bad = copy.deepcopy(parts)
    bad["INSERTS"][0]["media"] = _media(tmp_path, "pic.webp")
    second = open(write_jsx(tmp_path, bad, "two.jsx"), encoding="utf-8-sig").read()
    p = tmp_path / "all.jsx"
    p.write_text(good + "\n\n// ===== следующий таймлайн =====\n\n" + second,
                 encoding="utf-8-sig")
    errs = errors_of(str(p))
    assert has(errs, "AE не импортирует")
    assert has(errs, "таймлайн 2/2"), errs


def test_broken_syntax_is_error(tmp_path, parts):
    p = tmp_path / "broken.jsx"
    p.write_text("var CAM=[{;\napp.beginUndoGroup(", encoding="utf-8-sig")
    errs = errors_of(str(p))
    assert has(errs, "синтаксис") or has(errs, "не разбирается")


def test_undeclared_const_is_error(tmp_path, parts):
    """Настройка используется, но её `var` в шапке нет. Так вышло, когда геометрия
    переезжала в Python: `var INS_C2_PEAK/INS_C2_Y_FR` из шапки убрали, а обращение к
    INS_C2_Y_FR в ветке кам2 осталось. node --check молчит — синтаксис цел, — а AE
    падает «INS_C2_Y_FR is undefined» на первой же вставке кам2 (2026-08-12)."""
    raw = open(write_jsx(tmp_path, parts), encoding="utf-8-sig").read()
    p = tmp_path / "undeclared.jsx"
    p.write_text(raw + "\nvar y=Math.round(H*INS_C2_Y_FR);\n", encoding="utf-8-sig")
    assert has(errors_of(str(p)), "INS_C2_Y_FR")


def test_declared_consts_do_not_false_positive():
    """Проверка обязана молчать на боевом шаблоне: списки в одном `var` (`var W=…, H=…`),
    аргументы функций, ВЕРХНИЙ_РЕГИСТР внутри строк («ADBE …») и комментариев."""
    golden = os.path.join(HERE, "fixtures", "golden_geometry.jsx")
    rep = verify_jsx.Report(golden)
    verify_jsx.check_undeclared(open(golden, encoding="utf-8-sig").read(), rep)
    assert rep.errors == []


# --------------------------------------------------------------- вставки

def test_webp_insert_is_error(tmp_path, parts):
    """AE не импортирует webp вовсе: .jsx собирается, слоя в композиции нет."""
    parts["INSERTS"][0]["media"] = _media(tmp_path, "pic.webp")
    assert has(errors_of(write_jsx(tmp_path, parts)), "AE не импортирует")


def test_avif_insert_is_error(tmp_path, parts):
    parts["INSERTS"][0]["media"] = _media(tmp_path, "pic.avif")
    assert has(errors_of(write_jsx(tmp_path, parts)), "AE не импортирует")


def _jpeg(tmp_path, name, mode):
    """Настоящий JPEG в заданной цветовой модели (у _media внутри нули — PIL их не откроет)."""
    Image = pytest.importorskip("PIL.Image", reason="проверка цветовой модели требует pillow")
    p = tmp_path / name
    Image.new(mode, (8, 8), 0).save(str(p), "JPEG")
    return str(p).replace("\\", "\\\\")


def test_cmyk_jpeg_insert_is_error(tmp_path, parts):
    """Расширение читаемое, а модель — нет: CMYK-JPEG роняет importFile («Unsupported
    video bit depth») и вместе с ним ВЕСЬ скрипт, так что проекта не будет вовсе.
    Хуже пропавшего слоя, поэтому ошибка, а не предупреждение (2026-07-27, 09_ng18.jsx)."""
    parts["INSERTS"][0]["media"] = _jpeg(tmp_path, "stock.jpg", "CMYK")
    assert has(errors_of(write_jsx(tmp_path, parts)), "CMYK")


def test_rgb_jpeg_insert_is_clean(tmp_path, parts):
    """Контроль к предыдущему: обычный JPEG проверка трогать не должна."""
    parts["INSERTS"][0]["media"] = _jpeg(tmp_path, "ok.jpg", "RGB")
    assert not has(errors_of(write_jsx(tmp_path, parts)), "AE упадёт на импорте")


def test_jpeg_named_as_png_insert_is_error(tmp_path, parts):
    """JPEG с расширением .png (2026-09-11, restore-energy-2b968533.png): AE падает
    с «Input file doesn't seem to be a PNG file. (5027 :: 12)». verify_jsx обязан поймать."""
    Image = pytest.importorskip("PIL.Image", reason="нет Pillow")
    p = tmp_path / "fake.png"
    Image.new("RGB", (8, 8), 0).save(str(p), "JPEG")
    parts["INSERTS"][0]["media"] = str(p).replace("\\", "\\\\")
    errs = errors_of(write_jsx(tmp_path, parts))
    assert has(errs, "fake.png")
    assert has(errs, "внутри JPEG, а расширение .png")


def test_ae_format_constants_come_from_insertlib(tmp_path, parts):
    """Верификатор и конвертер to_ae_image обязаны знать про форматы ОДНО И ТО ЖЕ.
    Раньше обе тройки констант стояли копиями в двух файлах — добавь формат в один,
    и проверка начала бы ругаться на то, что конвертер уже чинит (или наоборот)."""
    from core import insertlib
    assert verify_jsx.AE_BAD_IMAGE is insertlib.AE_UNSUPPORTED
    assert verify_jsx.RASTER_EXT is insertlib.AE_RASTER
    assert verify_jsx.AE_OK_MODES is insertlib.AE_OK_MODES
    assert verify_jsx.AE_EXT_FORMAT is insertlib.AE_EXT_FORMAT
    assert verify_jsx.image_real_format is insertlib.image_real_format
    assert verify_jsx.AE_BAD_VCODEC is insertlib.AE_BAD_VCODEC


def test_photo_pointing_at_video_is_error(tmp_path, parts):
    """t решает в AE всё: фото = стоп-кадр с наездом, видео = футаж с whoosh.
    Автоподбор умел подставить mp4 под «фото» (2026-07-21)."""
    parts["INSERTS"][0]["media"] = _media(tmp_path, "oops.mp4")
    assert has(errors_of(write_jsx(tmp_path, parts)), "t=photo, а файл .mp4 — видео")


def test_video_pointing_at_image_is_error(tmp_path, parts):
    parts["INSERTS"][1]["media"] = _media(tmp_path, "oops.jpg")
    assert has(errors_of(write_jsx(tmp_path, parts)), "t=video, а файл .jpg — картинка")


def test_missing_media_file_is_error(tmp_path, parts):
    parts["INSERTS"][0]["media"] = str(tmp_path / "нет-такого.png").replace("\\", "\\\\")
    assert has(errors_of(write_jsx(tmp_path, parts)), "файла нет на диске")


def test_bad_insert_style_is_error(tmp_path, parts):
    parts["INSERTS"][0]["style"] = "cam3"
    assert has(errors_of(write_jsx(tmp_path, parts)), "style='cam3'".replace("'", "'"))


# --------------------------------------------------------------- субтитры

def test_raw_newline_in_word_is_error(tmp_path, parts):
    """Слово, перенесённое в титре Премьера (ДИГИДРО\\nТЕСТОСТЕРОНА), рвало JS-строку."""
    parts["SUBS"][0][2] = "ДИГИДРО\nТЕСТОСТЕРОНА"
    assert has(errors_of(write_jsx(tmp_path, parts)), "перенос строки")


def test_subs_out_of_order_is_error(tmp_path, parts):
    parts["SUBS"] = [parts["SUBS"][1], parts["SUBS"][0]]
    assert has(errors_of(write_jsx(tmp_path, parts)), "не по возрастанию времени")


def test_gend_before_end_is_error(tmp_path, parts):
    parts["SUBS"][0][5] = 5          # общий конец связки раньше самого слова
    assert has(errors_of(write_jsx(tmp_path, parts)), "gend < end")


def test_bad_hl_flag_is_error(tmp_path, parts):
    parts["SUBS"][0][3] = 2
    assert has(errors_of(write_jsx(tmp_path, parts)), "hl=2")


# --------------------------------------------------------------- камеры и рото

def test_overlapping_cam_clips_is_error(tmp_path, parts):
    parts["CAM"][0]["clips"][1][0] = 30      # начинается раньше конца предыдущего
    assert has(errors_of(write_jsx(tmp_path, parts)), "перехлёст")


def test_zero_length_clip_is_error(tmp_path, parts):
    parts["CAM"][0]["clips"][0][1] = 0
    assert has(errors_of(write_jsx(tmp_path, parts)), "start >= end")


def test_roto_ci_out_of_range_is_error(tmp_path, parts):
    """ci — индекс ОТ НУЛЯ в CAM (в шаблоне `var ci=(rr.ci||0)`); 2 камеры -> 0..1."""
    parts["ROTO"][0]["ci"] = 2
    assert has(errors_of(write_jsx(tmp_path, parts)), "вне диапазона камер")


def test_roto_mask_bigger_than_source_is_error(tmp_path, parts):
    parts["ROTO"][0]["mf"] = 0
    assert has(errors_of(write_jsx(tmp_path, parts)), "не может быть КРУПНЕЕ")


def test_empty_cam_is_error(tmp_path, parts):
    parts["CAM"] = []
    assert has(errors_of(write_jsx(tmp_path, parts)), "CAM пуст")


# --------------------------------------------------------------- интро и зум

def test_intro_words_times_mismatch_is_error(tmp_path, parts):
    parts["INTRO_GROUPS"][0][0]["times"] = [0.1]
    assert has(errors_of(write_jsx(tmp_path, parts)), "таймингов")


def test_empty_intro_group_is_error(tmp_path, parts):
    parts["INTRO_GROUPS"].append([])
    assert has(errors_of(write_jsx(tmp_path, parts)), "пустая группа")


def test_clip_without_intro_is_not_an_error(tmp_path, parts):
    """Клип без интро — это `[[]]`, так его отдаёт scene_plan. Проверка считала это
    «пустой группой» и валила предполёт рендера на ЛЮБОМ клипе без интро: в AE он не
    попадал вовсе (найдено 2026-08-14). Пустая группа среди непустых ловится выше."""
    parts["INTRO_GROUPS"] = [[]]
    assert not has(errors_of(write_jsx(tmp_path, parts)), "пустая группа")


def test_cam1scale_frames_must_ascend(tmp_path, parts):
    parts["CAM1_SCALE"] = [[62, 100], [0, 182]]
    assert has(errors_of(write_jsx(tmp_path, parts)), "не по возрастанию")


# --------------------------------------------------- сверка .jsx с исходным XML

def test_cross_check_with_source_xml(tmp_path, parts):
    """Число камер/слов/вставок в .jsx должно совпасть с parse_full исходника."""
    xml = os.path.join(HERE, "fixtures", "timeline_nosubs.xml")
    rep = verify_jsx.verify(write_jsx(tmp_path, parts), xml_path=xml)
    # в фикстуре 2 камеры, 0 субтитров, 0 вставок; в нашем .jsx — 2/2/2
    assert has(rep.errors, "слов-субтитров в XML 0, в .jsx 2")
    assert has(rep.errors, "вставок в XML 0, в .jsx 2")
    assert not has(rep.errors, "камер в XML")          # камеры совпали


# --------------------------------------------------------------- verify_ae

def _dump(nsubs=2, nintro=1, nins=2, nroto=1, cam_clips=(2, 1), order=None):
    """Синтетический дамп ae_inspect.jsx."""
    layers, idx = [], 1

    def add(name, source=None):
        nonlocal idx
        layers.append({"name": name, "index": idx, "source": source})
        idx += 1

    for group in (order or ["субтитры", "рото", "вставки/интро", "камеры"]):
        if group == "субтитры":
            add(verify_ae.SUBS_COMP)
        elif group == "рото":
            for _ in range(nroto):
                add("Рото камера")
                add("Рото маска")
        elif group == "вставки/интро":
            # интро-прекомпы лежат СЛОЯМИ в главном компе, на одном ярусе со вставками
            for i in range(nintro):
                add("текст интро" + (" %d" % (i + 1) if nintro > 1 else ""))
            for i in range(nins):
                add("Вставка: файл%d" % i)
        elif group == "камеры":
            for ci, n in enumerate(cam_clips):
                add("Камера %d" % (ci + 1))
                for _ in range(n):
                    add("клип", source="cam%d.mov" % (ci + 1))
    comps = [{"name": "Главный", "w": 1080, "h": 1920, "fps": 60, "layers": layers},
             {"name": verify_ae.SUBS_COMP, "layers": [{"name": "w%d" % i, "index": i}
                                                      for i in range(nsubs)]}]
    for i in range(nintro):
        comps.append({"name": "текст интро" + (" %d" % (i + 1) if nintro > 1 else ""),
                      "layers": []})
    return {"project": "test.aep", "comps": comps}


def _ae_structs(parts):
    """Структуры .jsx в том виде, в каком их ждёт verify_ae (пути не важны)."""
    return {k: parts[k] for k in verify_jsx.WANTED}


def test_ae_dump_matches_jsx(parts):
    rep = verify_jsx.Report("dump")
    verify_ae.check(_dump(), _ae_structs(parts), rep)
    assert rep.ok, rep.errors


def test_ae_missing_insert_layer_is_error(parts):
    """Классика: .jsx просил 2 вставки, в AE появилась одна (webp не импортировался)."""
    rep = verify_jsx.Report("dump")
    verify_ae.check(_dump(nins=1), _ae_structs(parts), rep)
    assert has(rep.errors, "вставок: в .jsx 2, в AE 1")
    assert has(rep.errors, "webp")          # подсказка про формат — часть сообщения


def test_ae_missing_subtitle_words_is_error(parts):
    rep = verify_jsx.Report("dump")
    verify_ae.check(_dump(nsubs=1), _ae_structs(parts), rep)
    assert has(rep.errors, "слов-субтитров: в .jsx 2, в AE 1")


def test_ae_inverted_stack_is_warned(parts):
    """Стек, перевёрнутый целиком, — предупреждение: канонический порядок описан в
    ARCHITECTURE.md, но проект правят руками, и перестановка бывает осознанной."""
    rep = verify_jsx.Report("dump")
    verify_ae.check(_dump(order=["камеры", "вставки/интро", "рото", "субтитры"]),
                    _ae_structs(parts), rep)
    assert has(rep.warns, "порядок слоёв")
    assert rep.ok, rep.errors


def test_ae_missing_camera_null_is_error(parts):
    dump = _dump()
    main = dump["comps"][0]
    main["layers"] = [L for L in main["layers"] if L["name"] != "Камера 2"]
    rep = verify_jsx.Report("dump")
    verify_ae.check(dump, _ae_structs(parts), rep)
    assert has(rep.errors, "нет нула «Камера 2»")


def test_ae_camera_clip_count_mismatch(parts):
    rep = verify_jsx.Report("dump")
    verify_ae.check(_dump(cam_clips=(1, 1)), _ae_structs(parts), rep)
    assert has(rep.errors, "клипов камер: в .jsx 3, в AE 2")


# ---------------------------------------- сборка «один .jsx на всё» (build_combined)

def _combined(nfiles=3):
    """Проект из `build_combined`: по главному компу на файл набора, и компы
    субтитров/интро у всех НАЗЫВАЮТСЯ ОДИНАКОВО. Найдено на реальном дампе
    ng1-4.aep: «самый слоистый комп» там не ответ, а угадывание."""
    d = _dump()
    main = d["comps"][0]
    for i in range(nfiles - 1):
        d["comps"].append({"name": "ng%d" % (i + 2), "w": 1080, "h": 1920, "fps": 60,
                           "layers": list(main["layers"])})
        d["comps"].append({"name": verify_ae.SUBS_COMP,
                           "layers": [{"name": "w%d" % j, "index": j} for j in range(50 + i)]})
    main["name"] = "ng1"
    return d


def test_combined_project_demands_explicit_comp(parts):
    rep = verify_jsx.Report("dump")
    verify_ae.check(_combined(), _ae_structs(parts), rep)
    assert has(rep.errors, "главных компов")
    assert has(rep.errors, "--comp ng1")


def test_combined_project_checks_named_comp(parts):
    rep = verify_jsx.Report("dump")
    verify_ae.check(_combined(), _ae_structs(parts), rep, comp_name="ng2")
    # у ng2 та же раскладка слоёв, что у ng1 -> расхождений быть не должно,
    # а комп субтитров ищется по числу слов, а не по имени
    assert rep.ok, rep.errors


def test_ae_counts_roto_mask_with_ae_name_suffix(parts):
    """AE дописывает « 2» к имени слоя, если такой уже есть. На сборке 10-16.aep
    так вышло у пяти масок (источником им стал авто-созданный комп
    «roto_<hash>.mp4 Comp 1»), и точное сравнение имени теряло их из счёта."""
    dump = _dump()
    for L in dump["comps"][0]["layers"]:
        if L["name"] == "Рото маска":
            L["name"] = "Рото маска 2"
            break
    rep = verify_jsx.Report("dump")
    verify_ae.check(dump, _ae_structs(parts), rep)
    assert rep.ok, rep.errors


def test_ae_front_video_and_photo_insert_are_different_tiers():
    """Фото едет прекомпом «INS <файл>» и лежит ПОД рото, видео кладётся футажом и
    поднимается НАД рото. Слои называются одинаково — различает только источник."""
    photo = {"name": "Вставка: pic", "index": 5, "source": "INS pic.png"}
    video = {"name": "Вставка: clip", "index": 2, "source": "clip.mp4"}
    assert verify_ae._kind(photo) == "вставки/интро"
    assert verify_ae._kind(video) == "фронт-видео"


def test_ae_wrong_jsx_pairing_is_reported_once(parts):
    """Сверять проект с .jsx ОТ ДРУГОГО клипа — типовая ошибка руками. Раньше это
    давало полдюжины расхождений подряд; теперь одна внятная строка и стоп."""
    dump = _dump()
    for L in dump["comps"][0]["layers"]:
        if L.get("source"):
            L["source"] = "совсем-другой.mov"
    rep = verify_jsx.Report("dump")
    verify_ae.check(dump, _ae_structs(parts), rep)
    assert len(rep.errors) == 1, rep.errors
    assert has(rep.errors, "это .jsx от другого клипа")


def test_ae_manual_project_is_reported(parts):
    """Проект, собранный в AE руками с нуля: наших имён слоёв нет, сверять нечего."""
    dump = {"project": "ручной.aep",
            "comps": [{"name": "C1235", "w": 1080, "h": 1920, "fps": 60,
                       "layers": [{"name": "C1235.MP4", "index": 1, "source": "C1235.MP4"},
                                  {"name": "Pre-comp 1", "index": 2}]}]}
    rep = verify_jsx.Report("dump")
    verify_ae.check(dump, _ae_structs(parts), rep)
    # такой комп даже не попадает в кандидаты на главный — наших слоёв в нём нет
    assert has(rep.errors, "нет ни одного композа")


def test_ae_interleaved_transitions_are_not_an_error(parts):
    """Переход создаётся в цикле вставок и поднимается позже — чередование
    «переход, вставка, переход, вставка» есть во ВСЕХ реальных проектах.
    Непрерывность ярусов инвариантом не является, ошибкой быть не должна.
    «Whoosh» — звук в паре с переходом, z-порядка у него нет."""
    dump = _dump()
    layers = dump["comps"][0]["layers"]
    ins_at = next(i for i, L in enumerate(layers) if L["name"].startswith("Вставка: "))
    layers.insert(ins_at + 1, {"name": "Переход", "index": 0})
    for i, L in enumerate(layers):          # переиндексация сверху вниз
        L["index"] = i + 1
    rep = verify_jsx.Report("dump")
    verify_ae.check(dump, _ae_structs(parts), rep)
    assert rep.ok, rep.errors


def test_ae_transition_below_roto_is_error(parts):
    """Баг 2026-07-14: вспышка перехода горела ПОЗАДИ персонажа. Это ПРЕДУПРЕЖДЕНИЕ,
    а не ошибка: проект правят руками, и переставленный слой бывает осознанным."""
    dump = _dump()
    layers = dump["comps"][0]["layers"]
    cam_at = next(i for i, L in enumerate(layers) if L["name"].startswith("Камера "))
    layers.insert(cam_at, {"name": "Переход", "index": 0})   # ниже рото
    for i, L in enumerate(layers):
        L["index"] = i + 1
    rep = verify_jsx.Report("dump")
    verify_ae.check(dump, _ae_structs(parts), rep)
    assert has(rep.warns, "ожидается выше")
    assert rep.ok, "порядок слоёв не должен валить сверку: " + str(rep.errors)


def test_combined_project_unknown_comp_name(parts):
    rep = verify_jsx.Report("dump")
    verify_ae.check(_combined(), _ae_structs(parts), rep, comp_name="ng9")
    assert has(rep.errors, "компа «ng9» в проекте нет")
