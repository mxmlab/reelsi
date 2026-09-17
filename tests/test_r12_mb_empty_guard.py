# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Пустой монтаж не затирает нарезку (задание MB, п. 1).

ПОЧЕМУ эти тесты существуют. `xmlbuild.build` с пустым `segments` (и с кусками короче
кадра) не отказывал: цикл по кускам просто не выполнялся, и `fileio.atomic_text_write`
клал поверх XML пользователя валидный файл с нулём клипов и нулевой длительностью.
Пользователь видел это двумя путями:

* редактор: убрал последний блок -> «Сохранить в XML» (keep=[]) -> камерные клипы
  исчезли, а следующая строка роута падала на «Не нашёл видеодорожки с камерами»;
* классическая нарезка: немой дубль/скринкаст (vad вернул []) -> пустой XML записан,
  а клип помечен собранным («-> 01_….xml (0s, 0 сег., 0 суб.)», «Готово»).

Контракт: отказ поднимается ВНУТРИ `build` (вызывающих восемь, проверка нужна одна),
до открытия целевого файла; вызывающие показывают это человеку, а не трейсбеком.
Рядом — копия оригинала `<файл>.xml.bak` ровно один раз (`_backup_once`).

Запуск: py -3.10 -m pytest tests/test_r12_mb_empty_guard.py -q -p no:cacheprovider
"""
import gzip
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

H = {"Host": "127.0.0.1:5001"}
FIX = os.path.join(HERE, "fixtures")


@pytest.fixture
def fake_probe(monkeypatch):
    """ffprobe вместо настоящего: медиа фикстур на диске не лежит."""
    from core import xmlbuild
    monkeypatch.setattr(xmlbuild, "probe", lambda p, **k: {
        "dur_s": 60.0, "width": 1920, "height": 1080, "timecode": "01:00:00:00"})


@pytest.fixture
def client():
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def xml_subs(tmp_path):
    """Эталон с субтитрами (253 слова, 2 камеры) — копия в tmp_path, не в рабочей копии."""
    dst = str(tmp_path / "timeline.xml")
    with gzip.open(os.path.join(FIX, "timeline_subs.xml.gz"), "rb") as g, \
            open(dst, "wb") as f:
        shutil.copyfileobj(g, f)
    return dst


def _cam(tmp_path, name="cam1.mp4"):
    p = tmp_path / name
    p.write_bytes(b"video")
    return str(p)


def _state(path):
    """Байты файла и mtime_ns — «файл не тронут» проверяется по обоим."""
    st = os.stat(str(path))
    with open(str(path), "rb") as f:
        return f.read(), st.st_mtime_ns


# --------------------------------------------------------------------------- #
# 1. build: пустой монтаж
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("segments", [
    [],
    [(0.0, 0.0)],
    [(1.0, 1.0)],
    [(5.0, 5.004)],           # 0.004с < кадра 60 к/с: фильтр build её выбрасывает
    [(0.0, 0.001), (10.0, 10.001)],
])
def test_пустой_монтаж_отказывает_и_не_трогает_файл(tmp_path, fake_probe, segments):
    """Ни одного куска длиннее кадра — SystemExit, и ни байта файла, ни mtime."""
    from core import xmlbuild
    out = tmp_path / "out.xml"
    out.write_bytes("<xmeml>прошлая нарезка</xmeml>".encode("utf-8"))
    before = _state(out)

    with pytest.raises(SystemExit) as e:
        xmlbuild.build([_cam(tmp_path)], segments, [0.0], str(out))

    assert "Ничего не перезаписываю" in str(e.value), str(e.value)
    assert _state(out) == before, "build тронул файл при пустом монтаже"
    # ни .tmp от атомарной записи, ни .bak: до записи дело не дошло вовсе
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cam1.mp4", "out.xml"]


def test_нормальный_кусок_пишется(fake_probe, tmp_path):
    """Гард не мешает сборке: один нормальный кусок пишет таймлайн как раньше."""
    from core import xmlbuild
    out = tmp_path / "out.xml"

    info = xmlbuild.build([_cam(tmp_path)], [(0.0, 2.0)], [0.0], str(out))

    assert info["segments"] == 1 and info["total_frames"] == 120
    text = out.read_text(encoding="utf-8")
    assert "<xmeml" in text and "<duration>120</duration>" in text


# --------------------------------------------------------------------------- #
# 2. Роут редактора: keep=[] — отказ с человеческим текстом
# --------------------------------------------------------------------------- #
def test_роут_editor_save_с_пустым_keep_отказывает(client, xml_subs, fake_probe):
    """XML на диске не изменился, ответ — отказ роута с текстом гарда КАК ЕСТЬ."""
    before = _state(xml_subs)

    r = client.post("/api/editor_save", json={"xml": xml_subs, "keep": []}, headers=H)
    d = r.get_json()

    assert r.status_code == 200, r.data[:200]
    assert d.get("ok") is not True and d.get("error"), d
    assert "Ничего не перезаписываю" in d["error"], d
    # не «SystemExit: …» и не «ValueError: …» — человеку показываем сам отказ
    assert "SystemExit" not in d["error"] and "ValueError" not in d["error"], d
    assert _state(xml_subs) == before, "XML перезаписан пустым таймлайном"
    assert not os.path.exists(xml_subs + ".bak"), "отказ не должен трогать и копию оригинала"


# --------------------------------------------------------------------------- #
# 3. Классический путь нарезки: речи нет — файл не помечен готовым
# --------------------------------------------------------------------------- #
def test_нарезка_без_речи_не_перезаписывает_xml(tmp_path, monkeypatch, fake_probe):
    """vad вернул [] (немой дубль/скринкаст): прошлый XML цел, «готово» не печатаем."""
    import reelsi
    from core import sync, vad

    cam = _cam(tmp_path)
    out = tmp_path / "01_cam1.xml"
    out.write_bytes("<xmeml>прошлая нарезка</xmeml>".encode("utf-8"))
    before = _state(out)
    log = []

    def emit(line="", **vars):
        log.append(line.format(**vars) if vars else line)

    # ffmpeg не гоняем: аудио «извлекается» пустым файлом, речи в нём нет по подмене
    monkeypatch.setattr(sync, "extract_audio",
                        lambda src, dst, *a, **k: open(str(dst), "wb").close())
    monkeypatch.setattr(vad, "speech_intervals", lambda *a, **k: [])

    args = reelsi.build_parser().parse_args(
        ["--cam1", cam, "--single", "--no-subs", "--no-dedup"])

    with pytest.raises(RuntimeError) as e:
        reelsi.process_pair([cam], str(out), args, emit=emit)

    assert "Собирать нечего" in str(e.value), str(e.value)
    assert _state(out) == before, "XML перезаписан пустым таймлайном"
    assert any("XML не создан" in s and "Собирать нечего" in s for s in log), log
    assert not any(s.lstrip().startswith("-> ") for s in log), \
        f"клип помечен готовым, хотя собирать было нечего: {log}"
    assert not os.path.exists(str(tmp_path / "01_cam1.srt"))


# --------------------------------------------------------------------------- #
# 4. Копия оригинала: <файл>.xml.bak — один раз, из build
# --------------------------------------------------------------------------- #
def test_build_делает_копию_оригинала_один_раз(fake_probe, tmp_path):
    """Первый build кладёт .bak с прежним содержимым; второй его НЕ перезаписывает."""
    from core import xmlbuild
    original = "<xmeml>оригинал из Премьера</xmeml>".encode("utf-8")
    out = tmp_path / "out.xml"
    out.write_bytes(original)
    bak = tmp_path / "out.xml.bak"

    xmlbuild.build([_cam(tmp_path)], [(0.0, 1.0)], [0.0], str(out))
    assert bak.read_bytes() == original, "копия оригинала не создана или создана не с него"
    rebuilt = out.read_bytes()
    assert rebuilt != original, "XML не пересобран — тест ничего не проверяет"

    xmlbuild.build([_cam(tmp_path)], [(0.0, 2.0)], [0.0], str(out))
    assert bak.read_bytes() == original, ".bak перезаписан второй сборкой"
    assert out.read_bytes() != rebuilt, "вторая сборка не дошла до файла"

    # файла ещё нет — копировать нечего, .bak не появляется
    fresh = tmp_path / "fresh.xml"
    xmlbuild.build([_cam(tmp_path)], [(0.0, 1.0)], [0.0], str(fresh))
    assert not (tmp_path / "fresh.xml.bak").exists()
