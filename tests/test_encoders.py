# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Общий выбор видеокодека: `core/encoders` — авто по железу и настройка «Видеокодек».

ffmpeg здесь НЕ запускается: `subprocess.run` подменяется, и «работает»
отвечает только тем кодекам, что перечислены в наборе теста. Проверяется то, ради чего
модуль и заведён:

* «авто» на машине с NVIDIA даёт РОВНО прежнее поведение черновика (h264_nvenc и те же
  аргументы) — иначе правка тихо поменяла бы качество и скорость чернового рендера;
* мастер (прожиг LUT) идёт в HEVC 10 бит, и проба мастера проверяет ТОТ ЖЕ 10-битный
  формат: в 8 битах она врала бы в обе стороны;
* выбранное семейство, которое не работает, не роняет рендер — предупреждение и авто;
* недоступное на этой машине видно интерфейсу (`available`), а «Авто» подписано тем,
  что оно реально выберет (`auto`);
* настройка живёт в ai_config.json (`set_video_encoder`) и читается черновиком.

Запуск:  python -m pytest tests/test_encoders.py -q
"""
import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from flask import Flask

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import api  # noqa: E402
from core import draftrender, encoders  # noqa: E402
from core.aicut import catalog  # noqa: E402
from core.aicut import config as aicut_config  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_caches():
    """Кэши проб живут на процесс: между тестами обнуляем, иначе тест видит чужой выбор.

    Черновик помнит ПРОБЫ (`draftrender.reset_cache`), а не выбранный кодек: выбор он
    считает при каждом вызове, чтобы смена настройки действовала без перезапуска.

    Заодно это держит ответ GET /api/ai_config чистым: поле `video_encoders` появляется
    в нём только у уже посчитанного списка (эталон test_ai_config_actions снят без него),
    и прогон соседнего теста не должен его туда добавлять.
    """
    encoders.reset_cache()
    draftrender.reset_cache()
    yield
    encoders.reset_cache()
    draftrender.reset_cache()


class _Result:
    """Ответ подменённого subprocess.run: код возврата и пустые потоки."""

    def __init__(self, rc: int) -> None:
        self.returncode = rc
        self.stdout = ""
        self.stderr = ""


def _fake_os(monkeypatch: pytest.MonkeyPatch, system: str) -> None:
    """ОС — только для encoders: подменяем его модуль platform, а не общий."""
    monkeypatch.setattr(encoders, "platform", SimpleNamespace(system=lambda: system))


def _fake_ffmpeg(monkeypatch: pytest.MonkeyPatch, working) -> list:
    """Подмена subprocess.run в core.encoders: микро-энкод не запускается.

    «Работает» отвечает ровно тем кодекам, что перечислены в `working` (имя берётся из
    `-c:v` команды). Возвращает список команд — по нему видно, ЧТО и КАК пробовали.
    """
    cmds: list = []

    def run(cmd, **kw):
        cmds.append(list(cmd))
        codec = cmd[cmd.index("-c:v") + 1] if "-c:v" in cmd else ""
        return _Result(0 if codec in working else 1)

    monkeypatch.setattr(encoders, "subprocess", SimpleNamespace(run=run))
    return cmds


def _codec_of(cmd: list) -> str:
    """Имя кодека из команды ffmpeg (`-c:v <имя>`)."""
    return cmd[cmd.index("-c:v") + 1] if "-c:v" in cmd else ""


def _has(args: list, flag: str, value: str) -> bool:
    """`flag value` стоят в аргументах рядом (а не порознь)."""
    return flag in args and args[args.index(flag) + 1] == value


def _save_cfg(**extra) -> None:
    """Конфиг с одним профилем (пустой profiles load_ai_config игнорирует) + настройки."""
    cfg = {"active": "Тест", "profiles": {
        "Тест": {"provider": "lmstudio", "base_url": "http://localhost:1234/v1",
                 "api_key": "", "model": "тестовая"}}}
    cfg.update(extra)
    aicut_config.save_ai_config(cfg)


def _client():
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


# --------------------------------------------------------------------------- #
# 1. «Авто» на NVIDIA-машине = прежнее поведение черновика
# --------------------------------------------------------------------------- #
def test_draft_auto_on_nvidia_matches_old_behavior(monkeypatch):
    """Windows + рабочий nvenc: те же h264_nvenc и те же аргументы, что были до правки.

    Аргументы сверяются с `draftrender._codec_args` — имя оставлено как раз ради этого:
    «-preset p4 -cq q» у nvenc, битрейт у остальных (их флаги качества разъезжаются от
    версии ffmpeg к версии)."""
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, {"h264_nvenc"})
    choice = encoders.pick("draft", "auto")
    assert choice.family == "nvidia"
    assert choice.args == ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "28"]
    assert choice.args == draftrender._codec_args("h264_nvenc", 28, "4M")
    assert choice.label == "NVIDIA (NVENC)"


def test_draft_cpu_setting_gives_libx264(monkeypatch):
    """Выбор «cpu» — libx264 с теми же параметрами, что у CPU-пути черновика."""
    _fake_os(monkeypatch, "Windows")
    cmds = _fake_ffmpeg(monkeypatch, {"h264_nvenc"})
    choice = encoders.pick("draft", "cpu")
    assert choice.family == "cpu" and choice.args[1] == "libx264"
    assert choice.args == ["-c:v", "libx264", "-crf", "26", "-preset", "veryfast"]
    assert cmds == [], "выбор «cpu» не должен ничего пробовать"


# --------------------------------------------------------------------------- #
# 2. Мастер: HEVC 10 бит и порядок отката
# --------------------------------------------------------------------------- #
def test_master_auto_prefers_nvenc(monkeypatch):
    """Мастер на NVIDIA — hevc_nvenc: 10-битный кадр, квантователь 18, тег hvc1."""
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, {"hevc_nvenc"})
    args = encoders.pick("master", "auto").args
    assert args[:2] == ["-c:v", "hevc_nvenc"]
    assert _has(args, "-cq", "18")
    assert _has(args, "-pix_fmt", "p010le")
    assert args[-2:] == ["-tag:v", "hvc1"]


def test_master_falls_from_dead_nvenc_to_qsv(monkeypatch):
    """nvenc не работает, qsv работает — берём hevc_qsv (и он тоже 10-битный)."""
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, {"hevc_qsv"})
    choice = encoders.pick("master", "auto")
    assert choice.family == "intel"
    assert choice.args[:2] == ["-c:v", "hevc_qsv"]
    assert _has(choice.args, "-pix_fmt", "p010le")
    assert _has(choice.args, "-profile:v", "main10")


def test_master_nothing_works_is_libx265(monkeypatch):
    """Ничего не работает — libx265: без кодека видео не собрать вовсе."""
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, set())
    assert encoders.pick("master", "auto").args == [
        "-c:v", "libx265", "-crf", "18", "-preset", "medium",
        "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1"]


# --------------------------------------------------------------------------- #
# 3. Выбрано, но не работает
# --------------------------------------------------------------------------- #
def test_dead_chosen_family_warns_and_falls_to_auto(monkeypatch):
    """Выбрано «intel», qsv не работает: предупреждение в лог и авто (nvidia).

    Настройка переживает смену железа — падать из-за неё рендер не должен."""
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, {"hevc_nvenc"})
    warns: list = []
    monkeypatch.setattr(encoders.log, "warning",
                        lambda msg, *a: warns.append(msg % a if a else msg))
    choice = encoders.pick("master", "intel")
    assert choice.family == "nvidia"
    assert warns and "не прошёл пробу" in warns[0], f"предупреждения в лог не было: {warns}"


def test_unknown_setting_falls_to_auto(monkeypatch):
    """Мусор в настройке (конфиг от другой версии) — тоже авто, а не исключение."""
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, {"hevc_nvenc"})
    warns: list = []
    monkeypatch.setattr(encoders.log, "warning",
                        lambda msg, *a: warns.append(msg % a if a else msg))
    assert encoders.pick("master", "мусор").family == "nvidia"
    assert warns and "мусор" in warns[0]


# --------------------------------------------------------------------------- #
# 4. Проба мастера — 10 бит
# --------------------------------------------------------------------------- #
def test_master_probe_uses_10bit_pixel_format(monkeypatch):
    """Проба мастера идёт ТЕМИ ЖЕ аргументами, что уйдут в дело, — с p010le/main10.

    В 8 битах проба врёт в обе стороны: 10-битный энкод может не подняться там, где
    8-битный работает, и наоборот. У черновика 10 бит нет — и в его пробе тоже."""
    _fake_os(monkeypatch, "Windows")
    cmds = _fake_ffmpeg(monkeypatch, {"hevc_nvenc"})
    assert encoders.probe("nvidia", "master") is True
    cmd = cmds[-1]
    assert _codec_of(cmd) == "hevc_nvenc"
    assert _has(cmd, "-pix_fmt", "p010le")
    assert _has(cmd, "-profile:v", "main10")
    assert cmd[-3:] == ["-f", "null", "-"], "проба не пишет файл — только проверяет энкод"
    # кадр 256x256: NVENC отвергает мелкие кадры, и проба врала бы «недоступен»
    assert "color=black:s=256x256:d=0.1" in cmd
    assert "p010le" not in encoders.codec_args("h264_nvenc", "draft")


# --------------------------------------------------------------------------- #
# 5. Порядок по ОС
# --------------------------------------------------------------------------- #
def test_mac_order_starts_with_apple(monkeypatch):
    """Mac: первым идёт медиадвижок Apple — NVENC там нет вовсе."""
    _fake_os(monkeypatch, "Darwin")
    cmds = _fake_ffmpeg(monkeypatch, {"hevc_videotoolbox", "hevc_nvenc"})
    assert encoders.auto_order()[0] == "apple"
    assert encoders.pick("master", "auto").family == "apple"
    assert _codec_of(cmds[0]) == "hevc_videotoolbox"


def test_windows_amd_machine_falls_to_amf(monkeypatch):
    """Windows без NVIDIA: порядок nvidia → amd → intel, побеждает первый живой."""
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, {"h264_amf"})
    assert encoders.pick("draft", "auto").args[1] == "h264_amf"


# --------------------------------------------------------------------------- #
# available / auto: что видит интерфейс
# --------------------------------------------------------------------------- #
def test_available_lists_only_live_in_os_order(monkeypatch):
    """available — живые семейства в порядке предпочтения ОС; первый и есть «авто»."""
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, {"hevc_qsv", "libx265"})
    assert encoders.available("master") == ["intel", "cpu"]
    assert encoders.auto_family("master") == "intel"
    assert encoders.cached_available("master") == ["intel", "cpu"]


def test_available_reuses_probes(monkeypatch):
    """Проба — запуск ffmpeg: список считается один раз на процесс."""
    _fake_os(monkeypatch, "Windows")
    cmds = _fake_ffmpeg(monkeypatch, {"hevc_nvenc", "libx265"})
    first = encoders.available("master")
    assert len(cmds) == len(encoders.FAMILIES), "пробуют не каждое семейство"
    assert encoders.available("master") == first
    assert len(cmds) == len(encoders.FAMILIES), "список пересчитан — пробы не кэшированы"


def test_available_is_not_computed_before_asking(monkeypatch):
    """До первого вопроса списка нет: ответ GET /api/ai_config не должен ждать проб."""
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, set())
    assert encoders.cached_available("master") is None
    assert encoders.available("master") == []
    assert encoders.cached_available("master") == []


# --------------------------------------------------------------------------- #
# 6. Настройка: действие и роут
# --------------------------------------------------------------------------- #
def test_set_video_encoder_action_and_encoders_route(monkeypatch):
    """`set_video_encoder`: семейство ок, мусор — отказ; /api/encoders отдаёт живое."""
    monkeypatch.setattr(catalog, "ensure_catalog", lambda *a, **k: {})   # сеть в тесте не нужна
    client = _client()
    ok = client.post("/api/ai_config",
                     json={"action": "set_video_encoder", "value": "nvidia"}).get_json()
    assert ok.get("error") is None, ok
    assert ok["video_encoder"] == "nvidia"
    assert aicut_config.load_ai_config()["video_encoder"] == "nvidia"
    assert "video_encoders" not in ok, "список живых кодеков не посчитан — ждать пробы нельзя"

    auto = client.post("/api/ai_config",
                       json={"action": "set_video_encoder", "value": "auto"}).get_json()
    assert auto["video_encoder"] == "auto"

    bad = client.post("/api/ai_config",
                      json={"action": "set_video_encoder", "value": "x"}).get_json()
    assert bad["err"] == "video_encoder_invalid", bad
    assert aicut_config.load_ai_config()["video_encoder"] == "auto", "отказ записал значение"

    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, {"hevc_nvenc", "libx265"})
    live = client.get("/api/encoders").get_json()
    assert live["available"] == ["nvidia", "cpu"] and live["auto"] == "nvidia"
    # GET /api/ai_config после этого отдаёт тот же список — но из кэша, без проб
    cfg_answer = client.get("/api/ai_config").get_json()
    assert cfg_answer["video_encoder"] == "auto"
    assert cfg_answer["video_encoders"] == {"available": ["nvidia", "cpu"], "auto": "nvidia"}


# --------------------------------------------------------------------------- #
# 7. Черновик
# --------------------------------------------------------------------------- #
def test_hw_encoder_honours_cpu_setting(monkeypatch):
    """`video_encoder="cpu"` — аппаратного нет вовсе (черновик уходит на libx264)."""
    _save_cfg(video_encoder="cpu")
    _fake_os(monkeypatch, "Windows")
    cmds = _fake_ffmpeg(monkeypatch, {"h264_nvenc"})
    assert draftrender.hw_encoder() is None
    assert cmds == [], "выбор «cpu» не должен пробовать аппаратные кодеки"
    assert encoders.pick("draft").args[1] == "libx264"


def test_hw_encoder_auto_with_nvenc(monkeypatch):
    """При «авто» и рабочем nvenc черновик берёт h264_nvenc — прежний путь."""
    _save_cfg()
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, {"h264_nvenc"})
    assert draftrender.hw_encoder() == "h264_nvenc"
    hwc = draftrender._codec_args(draftrender.hw_encoder(), 30, "3M")
    assert hwc == ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "30"]


def test_hw_encoder_none_when_nothing_works(monkeypatch):
    """Совсем без аппаратного — None (как раньше), а не имя кодека."""
    _save_cfg()
    _fake_os(monkeypatch, "Windows")
    _fake_ffmpeg(monkeypatch, set())
    assert draftrender.hw_encoder() is None


# --------------------------------------------------------------------------- #
# 8. Цвет: видеодиапазон и матрица BT.709
# --------------------------------------------------------------------------- #
# Кадры рендера без AE приходят снимком экрана: полный RGB без матрицы. Уходили в файл
# как есть — и файл выходил `color_range=pc, color_space=bt470bg`: тени и полутени
# уезжали вверх (замер владельца: тени +19%, средние тона +15%, светлые +3%), а с
# эталоном AE (`tv, bt709`) он расходился. Здесь стерегутся оба шага: ФИЛЬТР перевода
# (иначе теги — надпись на неверных числах) и САМИ ТЕГИ у каждого кодека.
def test_rgb_is_converted_into_video_range_bt709():
    """Фильтр перевода: `scale=out_range=tv:out_color_matrix=bt709` и формат кадра.

    Формат называется в том же фильтре: отданный «как есть» кадр кодировщик перевёл бы
    второй раз — уже без матрицы, и цвет уехал бы молча.
    """
    f = encoders.color_filter("yuv420p10le")
    assert f == "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p10le", f
    assert encoders.color_filter("yuv420p") == \
        "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p"
    # Имена значений — те же, что у эталона AE (ffprobe по его ролику).
    assert (encoders.COLOR_RANGE, encoders.COLOR_MATRIX) == ("tv", "bt709")


def test_color_args_carry_the_filter_and_all_four_tags():
    """В аргументах цвета есть и фильтр, и все четыре тега эталона AE."""
    args = encoders.color_args("h264_nvenc", "yuv420p")
    assert _has(args, "-vf", encoders.color_filter("yuv420p")), args
    for flag, value in (("-color_range", "tv"), ("-colorspace", "bt709"),
                        ("-color_primaries", "bt709"), ("-color_trc", "bt709")):
        assert _has(args, flag, value), f"нет тега {flag} {value}: {args}"
    # Фильтр идёт ПЕРВЫМ: он относится к выводу кодека, а теги — его параметры.
    assert args[0] == "-vf", args


def test_every_codec_gets_the_same_color():
    """У КАЖДОГО кодека обоих назначений — тот же фильтр и те же теги.

    Кодек рендера выбирается по железу (`pick`), и разойдись цвет между семействами —
    «тени уехали» возвращалось бы на одной машине и не возвращалось на другой.
    """
    pix = {"draft": "yuv420p", "master": "yuv420p10le"}
    for purpose, table in (("draft", encoders.DRAFT_CODECS),
                           ("master", encoders.MASTER_CODECS)):
        for family, codec in table.items():
            # Формат кадра — из аргументов САМОГО кодека (у мастера это 10 бит).
            args = encoders.color_args(codec, pix[purpose])
            assert _has(args, "-vf", encoders.color_filter(pix[purpose])), (family, args)
            for flag, value in (("-color_range", "tv"), ("-colorspace", "bt709"),
                                ("-color_primaries", "bt709"), ("-color_trc", "bt709")):
                assert _has(args, flag, value), (family, codec, flag, args)


def test_cpu_codecs_write_the_tags_into_the_bitstream():
    """x264/x265 получают теги ещё и в VUI битстрима (`-x264-params`/`-x265-params`).

    Замер на этой машине (ffmpeg 8.0): общие ключи `-color_primaries`/`-color_trc` до VUI
    у libx264/libx265 НЕ доезжают — в файле остаются `unknown, unknown`. Параметрами
    кодека теги доходят все четыре, и это видно `ffprobe` по готовому файлу (живая
    проверка ниже).
    """
    x264 = encoders.color_args("libx264", "yuv420p")
    assert _has(x264, "-x264-params",
                "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=tv"), x264
    x265 = encoders.color_args("libx265", "yuv420p10le")
    assert _has(x265, "-x265-params",
                "log-level=none:colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited"), x265
    # Не-CPU кодеки этих ключей не получают: их у них нет вовсе.
    for codec in ("h264_nvenc", "hevc_qsv", "h264_amf", "h264_videotoolbox"):
        args = encoders.color_args(codec, "yuv420p")
        assert "-x264-params" not in args and "-x265-params" not in args, (codec, args)


def test_vui_bsf_writes_the_tags_for_the_codecs_that_lose_them():
    """Железным кодекам теги VUI пишет битстрим-фильтр, а не параметры кодека.

    Замер на этой машине (ffmpeg 8.0, мастер по умолчанию `hevc_nvenc`): общие
    `-color_primaries/-color_trc` до битстрима не доезжают — в готовом ролике владельца
    было `color_range=tv, color_space=bt709, color_transfer=unknown,
    color_primaries=unknown`. `-bsf:v hevc_metadata=…` дописывает VUI уже готового
    потока, и все четыре тега на месте. У libx264/libx265 VUI пишут параметры кодека
    (`-x264-params`/`-x265-params`, проверены замером) — второй раз теги туда не едут.
    """
    expected = "colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0"
    for codec, family in (("hevc_nvenc", "hevc"), ("hevc_qsv", "hevc"), ("hevc_amf", "hevc"),
                          ("hevc_videotoolbox", "hevc"), ("h264_nvenc", "h264"),
                          ("h264_qsv", "h264"), ("h264_amf", "h264"),
                          ("h264_videotoolbox", "h264")):
        args = encoders.color_args(codec, "yuv420p")
        assert _has(args, "-bsf:v", "%s_metadata=%s" % (family, expected)), (codec, args)
    for codec in ("libx264", "libx265"):
        args = encoders.color_args(codec, "yuv420p")
        assert "-bsf:v" not in args, (codec, args)


def test_segment_command_puts_the_filter_between_input_and_codec():
    """В командной строке куска цветовой фильтр стоит ПОСЛЕ входа и ДО кодека.

    `-vf` — опция вывода: поставленная до `-i`, она читается опцией входа и ffmpeg
    падает на «Option vf not found». Порядок здесь и есть контракт.
    """
    import core.webrender as wr

    args = encoders.color_args("libx265", "yuv420p10le")
    cmd = wr._segment_cmd("seg.mp4", ["-c:v", "libx265", "-pix_fmt", "yuv420p10le"], args, 60.0)
    i_in = cmd.index("-i")
    i_vf = cmd.index("-vf")
    i_codec = cmd.index("-c:v", i_in + 1)
    assert i_in < i_vf < i_codec, cmd
    assert cmd[i_vf + 1] == encoders.color_filter("yuv420p10le"), cmd
    for flag, value in (("-color_range", "tv"), ("-colorspace", "bt709"),
                        ("-color_primaries", "bt709"), ("-color_trc", "bt709")):
        assert _has(cmd, flag, value), cmd


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
live_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE),
                                 reason="живая проверка цвета требует ffmpeg и ffprobe в PATH")


@live_ffmpeg
def test_live_encode_writes_the_tags_and_the_copy_keeps_them(tmp_path):
    """Живой прогон: цветовые теги в файле — ровно как у эталона AE, и переживают копию.

    Кодируется ОДИН кадр (быстро) тем же путём, что рендер: фильтр перевода по аргументам
    `core.encoders`, затем `ffprobe` по готовому файлу. Второй шаг — копия видео с
    дорожкой звука (`-c:v copy`): именно так рендер накладывает звук, и теги обязаны
    остаться (иначе цвет терялся бы на последнем шаге).
    """
    seg = tmp_path / "seg.mp4"
    color = encoders.color_args("libx265", "yuv420p10le")
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30",
         "-frames:v", "1", *color,
         "-c:v", "libx265", "-crf", "28", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", str(seg)],
        check=True, capture_output=True)
    got = _probe_color(str(seg))
    assert got == {"color_range": "tv", "color_space": "bt709",
                   "color_transfer": "bt709", "color_primaries": "bt709"}, got
    # Копия потока (как наложение звука в рендере: видео идёт `-c:v copy`) теги не теряет.
    final = tmp_path / "final.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(seg), "-c", "copy", str(final)],
                   check=True, capture_output=True)
    assert _probe_color(str(final)) == got


def _probe_color(path: str) -> dict:
    """Цветовые поля видеопотока файла — глазами ffprobe."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=color_range,color_space,color_transfer,color_primaries",
                        "-of", "json", path], check=True, capture_output=True, text=True,
                       encoding="utf-8")
    st = json.loads(r.stdout)["streams"][0]
    return {k: st.get(k, "unknown") for k in
            ("color_range", "color_space", "color_transfer", "color_primaries")}


AE_COLOR = {"color_range": "tv", "color_space": "bt709",
            "color_transfer": "bt709", "color_primaries": "bt709"}


@live_ffmpeg
def test_live_chain_segment_concat_mux_keeps_all_four_tags(tmp_path):
    """ВСЯ цепочка рендера: сегменты -> склейка `-c copy` -> микс со звуком `-c:v copy`.

    Цвет терялся не на склейке: у мастера (`hevc_nvenc` на этой машине) тегов не было уже
    в сегменте, а дальше оба шага идут копированием и несут то, что есть. Проверка идёт
    по шагам — тем же кодом, что у рендера: кодировщик выбирает `encoders.pick("master")`,
    склейку собирает `core.webrender._concat_cmd`, микс — `core.webrender_audio`.
    """
    import core.webrender as wr
    from core import webrender_audio as wa

    codec = encoders.pick("master")
    pix = wr._codec_pix_fmt(codec.args)
    color = encoders.color_args(codec.args[1], pix)
    segs = []
    for k, pat in enumerate(("testsrc2=size=256x480:rate=30", "smptebars=size=256x480:rate=30")):
        seg = tmp_path / ("seg%d.mp4" % k)
        subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                        pat + ":duration=0.5", *color, *codec.args, "-an", str(seg)],
                       check=True, capture_output=True)
        got = _probe_color(str(seg))
        assert got == AE_COLOR, ("сегмент %d (%s): %s" % (k, codec.args[1], got))
        segs.append(str(seg))
    cat = tmp_path / "cat.mp4"
    lst = tmp_path / "list.txt"
    with open(lst, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join("file '%s'\n" % s.replace("\\", "/") for s in segs))
    subprocess.run(wr._concat_cmd(str(lst), str(cat)), check=True, capture_output=True)
    assert _probe_color(str(cat)) == AE_COLOR, "склейка посеяла цветовые теги"
    wav = tmp_path / "a.wav"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:a", "pcm_s16le", str(wav)],
                   check=True, capture_output=True)
    final = tmp_path / "final.mp4"
    plan = {"audio": {"music_path": str(wav), "music_db": -20.0}}
    # dur — длина микса: без него граф собирается на ноль секунд и файл выходит пустым.
    out = wa.mix(plan, str(cat), str(final), dur=1.0)
    assert out and os.path.isfile(str(final)), out
    got = _probe_color(str(final))
    assert got == AE_COLOR, ("итоговый файл (микс со звуком): %s" % got)


# --------------------------------------------------------------------------- #
# 9. Мутации: без правки проверки обязаны краснеть
# --------------------------------------------------------------------------- #
def _color_args_from(broken_src: str) -> list:
    """`color_args` из ИСПОРЧЕННОГО исходника core/encoders.py — без фильтра перевода."""
    import importlib.util

    path = os.path.join(ROOT, "core", "_encoders_mutated.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(broken_src)
    try:
        spec = importlib.util.spec_from_file_location("_encoders_mutated", path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_encoders_mutated"] = mod
        spec.loader.exec_module(mod)
        return list(mod.color_args("libx265", "yuv420p10le"))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_mutation_without_the_conversion_filter_is_caught():
    """Мутация 2: убрать фильтр перевода (оставить одни теги) — проверка обязана покраснеть.

    Проверка не «на словах»: тот же модуль собирается из ИСПОРЧЕННОГО исходника, где из
    `COLOR_FILTER` выкинута сама `scale`, и тест требует, чтобы проверка фильтра вышла
    красной. Так сторож не сможет молча перестать ловить «теги есть, а числа неверные» —
    ровно то состояние, из-за которого тени и полутени уезжали вверх.
    """
    src_path = os.path.join(ROOT, "core", "encoders.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    marker = ('COLOR_FILTER = "scale=out_range=%s:out_color_matrix=%s,format=%%s" '
              "% (COLOR_RANGE,\n                                                                     "
              "COLOR_MATRIX)")
    assert marker in src, "строка фильтра изменилась — мутация устарела"
    broken = src.replace(
        marker,
        'COLOR_FILTER = "format=%s"   # МУТАЦИЯ: перевод в видеодиапазон выброшен')
    args = _color_args_from(broken)
    # Теги на месте — старая проверка «теги есть» осталась бы зелёной.
    assert _has(args, "-color_range", "tv") and _has(args, "-colorspace", "bt709"), args
    # А проверка фильтра обязана упасть: перевода в видеодиапазон больше нет.
    assert _has(args, "-vf", encoders.color_filter("yuv420p10le")) is False, (
        "мутация не поймана: без `scale` проверка фильтра осталась зелёной")

