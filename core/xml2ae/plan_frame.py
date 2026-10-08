# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Камеры плана и рамка кадра спикера (остаток распила scene_plan).

`scene_plan` был одной функцией на тысячи строк; этот модуль забирает из него блок
камер ПЛАНА — тот, что раньше стоял между результатом `plan_audio` и подстановками
шаблона:

* `cams` плана (`plan["cams"]`) — те же словари клипов, что читает предпросмотр;
* рамка кадра спикера (поле `frame` профиля спикера): доля рамки, сдвиг слоя в px кадра
  (`core/frame.frame_shift` — единственный зажим рамки) и множитель масштаба на камеру;
* готовые строки шаблона `CAM` (`cams_js`) и три подстановки под рамку
  (`cam_frame_pos`, `roto_frame_scale`, `roto_frame_pos`).

Перенос ПОСТРОЧНЫЙ: поведение, числа и текст подстановок не менялись ни на байт
(проверяется эталоном fixtures/golden_geometry.jsx и побайтовым сравнением .jsx/плана).
Имена локальных переменных оставлены как в scene_plan — тело перенесено дословно, а входы
распаковываются в преамбуле.

Вход — один неизменяемый `FrameInputs`, выход — один `FramePlan` с ровно теми именами,
что `scene_plan` читает дальше. Спикер ролика берётся из сайдкара рядом с XML тем же путём,
что у формата кадра (`core/frame.xml_frames`), — второй двери к профилю спикера здесь нет.
Размер исходника приходит параметром `media_dims` (его отдаёт build.py): второй копии
двери «размеры файла» тут не заводится — `_media_dims` подменяют в модуле build
(tests/test_cam_frame.py), и своя ссылка на `layout._media_dims` обходила бы подмену:
сдвиг рамки молча выходил нулевым.
"""
from dataclasses import dataclass
from typing import Any, Callable

from core import frame as _frame

from .jsutil import _jd, _r


@dataclass(frozen=True)
class FrameInputs:
    """Вход блока камер плана: всё, что `scene_plan` знает к моменту вызова.

    Поля названы как локальные переменные scene_plan: `cams` — камеры из `parse_full`
    (тело читает `path`/`name`/`clips`), `meta` — ролик (`w`/`h` кадра уже приведены к
    формату спикера), `xml_path` — по нему рядом лежит сайдкар профиля спикера
    (`core/frame.xml_frames`).
    """
    cams: list
    meta: dict
    xml_path: str
    # Размеры исходника камеры: build.py отдаёт свой _media_dims (тесты подменяют его
    # в build — тем же правилом, что у вставок в plan_inserts.py). Модуль не берёт
    # layout._media_dims сам: подмена в build до своей ссылки не достала бы.
    media_dims: Callable[[str], Any]


@dataclass(frozen=True)
class FramePlan:
    """Выход блока: камеры плана и готовые подстановки шаблона под рамку кадра.

    `cams_plan` уезжает в `plan["cams"]` (по нему считает рото и рисует предпросмотр),
    `cams_js` — строка `var CAM=`, три остальных поля — подстановки шаблона. Ни одной
    рамки в ролике все три пустые, и .jsx остаётся прежним (golden): строку масштаба
    клипов это не касается — она одна на все камеры (fitS × zoom рамки).
    """
    cams_plan: list
    cams_js: str
    cam_frame_pos: str
    roto_frame_scale: str
    roto_frame_pos: str


def _speaker_frames(xml_path: str) -> dict[str, dict[str, float]]:
    """Поле `frame` профиля спикера ролика: спикер — в сайдкаре рядом с XML.

    Тем же путём его берут формат кадра (`core/frame.output_frame_size`), LUT
    (`core/lutbake`) и обработка голоса (`core/voicefx`). Геометрия рамки — в
    `core/frame.py` (`xml_frames`), здесь только имя, по которому её спрашивают.
    """
    return _frame.xml_frames(xml_path)


def apply_frame(fw: int, fh: int, meta: dict, cams: list, emit: Any) -> None:
    """Привести кадр плана к кадру ролика и проверить камеры.

    Кадр ролика (`fw`/`fh`) считает вызывающий ДО разбора XML: формат из профиля спикера
    главнее того, что лежит в XML, но ТОЛЬКО когда задан в профиле явно
    (`core/frame.ensure_frame`). Формат выбирают в профиле, а XML пишется нарезкой, и между
    этими событиями формат мог поменяться: тогда XML пересобирается в новый кадр тем же
    путём, что «Сохранить» в редакторе нарезки, — и только если разошлись ПРОПОРЦИИ
    (XML 2160×3840 при формате 9:16 не трогаем: тот же формат, просто крупнее). Здесь, а не
    в роуте, — потому что сюда приходят ВСЕ сборки: сборка .jsx, превью шага 3 (`/api/scene`),
    черновик (`virtual_edl`) и набор. Профиля нет или формат в нём не задан — кадр берётся
    из самого XML, как раньше.

    `meta` — уже разобранный XML, и его w/h правятся ЗДЕСЬ: кадр плана — кадр ролика
    (формат, заданный явно), даже если XML пересобрать не удалось (битый сайдкар, нет
    исходников под рукой): показать и собрать ролик в заказанном формате честнее, чем
    в том, что осталось в XML. Premiere при этом покажет XML, и о расхождении в логе уже
    сказано предупреждением `ensure_frame`.

    Диагностика камер — здесь же: пустой путь к файлу камеры рождает пустой нул (жалоба
    1-кам), а отсутствие камер вовсе — ошибка сборки. ValueError, а НЕ SystemExit:
    вызывающие ловят только Exception, поэтому SystemExit пролетал сквозь них — /api/to_ae
    отдавал 500-HTML вместо {error}, а фоновая сборка молча писала «Сборка завершена»
    с пустым results.
    """
    _fw, _fh = int(fw), int(fh)
    if (_fw, _fh) != (int(meta["w"]), int(meta["h"])):
        emit("⚠ формат кадра: XML в {ow}×{oh}, а ролик собирается в {nw}×{nh} — "
             "пересобери XML в редакторе нарезки", ow=meta["w"], oh=meta["h"], nw=_fw, nh=_fh)
        meta["w"], meta["h"] = int(_fw), int(_fh)
    if not cams:
        raise ValueError("Не нашёл видеодорожки с камерами в XML.")
    for _ci, _c in enumerate(cams):      # диагностика 1-кам «нет названий нулов»: пустой путь
        if not (_c.get("path") or "").strip():
            emit("⚠ Камера {cam} без пути к файлу (нул создастся пустым — проверь XML).",
                 cam=_ci + 1)


def plan_frame(inp: FrameInputs) -> FramePlan:
    """Собрать камеры плана и подстановки рамки кадра: чистая функция от `FrameInputs`.

    Вызов `core/frame.xml_frames` — единственное чтение с диска: сайдкар профиля спикера
    читают здесь ОДИН раз, второй копии чтения в сборке нет. Размеры исходника — дверью
    из `FrameInputs.media_dims` (её отдаёт build.py), своей копии `layout._media_dims` тут нет.
    """
    cams, meta, xml_path = inp.cams, inp.meta, inp.xml_path
    cams_plan = []
    for ci, c in enumerate(cams):
        cams_plan.append({"ci": ci, "path": c["path"] or "", "name": c["name"],
                          "clips": [[s, e, i, o, bool(en), _r(sc)]
                                    for s, e, i, o, en, sc in c["clips"]]})
    # ---- рамка кадра камер (поле `frame` профиля спикера) ----
    # Спикер — из сайдкара рядом с XML: тем же путём его берут формат кадра
    # (core/frame.output_frame_size), LUT и обработка голоса. Размер исходника ведает
    # только After Effects, поэтому долями рамки уезжает и готовая геометрия:
    # сдвиг слоя в px кадра (core/frame.frame_shift — единственный зажим рамки)
    # и множитель масштаба. Клип, рото-копия и Basic Motion в Premiere берут ОДНИ
    # И ТЕ ЖЕ числа отсюда, второй копии формулы нет.
    _frames = _frame.xml_frames(xml_path)
    for _fr_ent, _fr_cam in zip(cams_plan, cams):
        _fr = _frame.frame_of(_frames, _fr_ent["ci"] + 1)
        if _frame.is_frame_default(_fr):
            continue                     # камера без правок — ключа нет (golden прежний)
        _dims = inp.media_dims(_fr_cam.get("path") or "")
        _dx, _dy = (_frame.frame_shift(_fr, _dims[0], _dims[1], meta["w"], meta["h"])
                    if _dims else (0.0, 0.0))
        _fr_ent["frame"] = {"x": _fr["x"], "y": _fr["y"], "zoom": _fr["zoom"],
                            "dx": _dx, "dy": _dy}
    cams_js = _jd([{"path": c["path"], "name": c["name"], "clips": c["clips"],
                    **({"frame": c["frame"]} if "frame" in c else {})} for c in cams_plan])
    # Подстановки шаблона под рамку. Ни одной рамки в ролике — все три пустые, и .jsx
    # остаётся прежним, кроме строки масштаба клипов (она перешла на fitS у всех камер).
    # Числа сдвига считает Python (core/frame.frame_shift), ExtendScript только применяет:
    # размера исходника план не знает, а масштаб слоя AE считает сам (fitS).
    cam_frame_pos = roto_frame_scale = roto_frame_pos = ""
    if any("frame" in c for c in cams_plan):
        cam_frame_pos = (
            "            // рамка кадра: сдвиг исходника — в МИРОВЫХ координатах композиции\n"
            "            // и ДО привязки к нулу: AE при присвоении parent сохраняет мировое\n"
            "            // положение слоя, а числа рамки посчитаны от центра кадра\n"
            "            if (track.frame) try{ lay.property(\"ADBE Transform Group\")\n"
            "                .property(\"ADBE Position\").setValue([W/2+track.frame.dx, H/2+track.frame.dy]); }catch(e){}\n")
        roto_frame_scale = (
            "\n            // рамка кадра своей камеры: у рото-копии тот же масштаб, что у клипа\n"
            "            var rfr = CAM[ci] && CAM[ci].frame;\n"
            "            if (rfr) rsc = rfit*rfr.zoom/100*(ci==0?CAM1_FIT/100:1);")
        roto_frame_pos = (
            "\n            // и тот же сдвиг: копия обязана лежать пиксель-в-пиксель с кадром камеры\n"
            "            if (rfr) try{ cc.property(\"ADBE Transform Group\").property(\"ADBE Position\")\n"
            "                .setValue([rfr.dx,rfr.dy]); }catch(e){}\n"
            "            if (rfr) try{ mk.property(\"ADBE Transform Group\").property(\"ADBE Position\")\n"
            "                .setValue([rfr.dx,rfr.dy]); }catch(e){}")
    return FramePlan(cams_plan=cams_plan, cams_js=cams_js, cam_frame_pos=cam_frame_pos,
                     roto_frame_scale=roto_frame_scale, roto_frame_pos=roto_frame_pos)
