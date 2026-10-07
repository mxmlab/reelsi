# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Правки по следам прогона на Linux: интерфейс и диагностика.

Каждая проверка сторожит ровно тот дефект, из-за которого правка и делалась:

1. **`setStepProfile` перерисовывает «Ум»** (`static/app/10-settings.js`). Уровни
   размышления строятся по модели НОВОГО профиля шага; без перерисовки селект
   показывал уровни прежней модели (у неё их могло не быть вовсе — и он был бы
   `disabled`) до перезахода в настройки.
2. **`/api/status` отдаёт в `results` ПОЛНЫЕ пути XML** (`api/jobs.py`), клиент их не
   склеивает. Склейка в JS своим разделителем на Linux давала «<папка>\имя.xml» —
   одно имя файла с обратным слэшем, которого на диске нет.
3. **`cutAdopt` берёт путь как есть и не плодит второй клип**: `clipByXml` сравнивает
   пути нормализованно (тот же ключ, что `normInsPath`: разделители и регистр), а
   сохранённые испорченные записи чинятся при загрузке состояния.
4. **Отказ «уже идёт» называет задачу-владельца лока и её прогресс**, а ход сборки
   прокси превью виден в общей панели «Логи»: сообщение посылало человека туда, где
   строк не было.
5. **doctor**: тип исключения в строке отказа опционального модуля и отсутствие
   ложного предупреждения о версии Python, которую проверяет CI.

Запуск:  py -3.10 -m pytest tests/test_ui_fixes_linux.py -q
"""
from __future__ import annotations

import collections
import io
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from typing import Any

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

APP = os.path.join(ROOT, "static", "app")
SETTINGS_JS = os.path.join(APP, "10-settings.js")
QUEUE_JS = os.path.join(APP, "40-queue.js")
BOOT_JS = os.path.join(APP, "99-boot.js")
VIEW_JS = os.path.join(APP, "85-inserts-view.js")
CI_YML = os.path.join(ROOT, ".github", "workflows", "ci.yml")

node = pytest.mark.skipif(not shutil.which("node"), reason="требуется node в PATH")


def _read(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def _extract(src: str, marker: str) -> str:
    """Кусок исходника от marker до парной закрывающей скобки."""
    assert marker in src, "не нашёл в исходнике: %s" % marker
    start = src.index(marker)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("не закрылась скобка у %s" % marker)


def _fn(path: str, name: str) -> str:
    src = _read(path)
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, "не нашёл функцию %s в %s" % (name, os.path.basename(path))
    return _extract(src, m.group(0))


def _run_node(tmp_path: Any, name: str, script: str) -> Any:
    """Прогнать стенд под node и вернуть разобранный JSON (последняя строка stdout)."""
    f = tmp_path / name
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True,
                          encoding="utf-8-sig", errors="replace", timeout=60)
    assert proc.returncode == 0, "node: %s" % (proc.stderr or proc.stdout)[:600]
    lines = [x.strip() for x in proc.stdout.strip().splitlines() if x.strip()]
    assert lines, "node ничего не вывел"
    return json.loads(lines[-1])


_TAIL = r"""
require('fs').writeSync(1, JSON.stringify(out)+'\n');
process.exit(0);
"""


# --------------------------------------------------------------------------- #
# 1. Смена модели шага перерисовывает уровни «Ум»
# --------------------------------------------------------------------------- #
@node
def test_set_step_profile_redraws_reasoning_levels(tmp_path: Any) -> None:
    """`setStepProfile` зовёт `fillStepReasoning` — список уровней идёт от новой модели.

    Мутация «убрать fillStepReasoning» валит проверку: селект «Ум» остаётся с
    уровнями прежней модели (у профиля без reasoning он вдобавок `disabled`).
    """
    script = """
const calls=[];
let AICFG={active:'Первый',profiles:{'Первый':{model:'m1'},'Второй':{model:'m2'}},
           step_profiles:{},reasoning_steps:{},reasoning_effective:{}};
function t(s){return s;}
function toast(m){calls.push('toast');}
function errText(d){return (d&&d.error)||String(d);}
function fillStepProfiles(){calls.push('profiles');}
function fillStepReasoning(){calls.push('reasoning');}
function cutSummary(){calls.push('cut');}
function markupSummary(){calls.push('markup');}
async function fetch(url,opt){
  return {json:async()=>({step_profiles:{cut:'Второй'},
                          reasoning_effective:{cut:'high'}})};
}
__SETPROFILE__
const out={};
(async()=>{
  await setStepProfile({dataset:{prof:'cut'},value:'Второй'});
  out.calls=calls;
  out.profile=AICFG.step_profiles.cut;
  require('fs').writeSync(1, JSON.stringify(out)+'\\n');
  process.exit(0);
})();
"""
    script = script.replace("__SETPROFILE__", _fn(SETTINGS_JS, "setStepProfile"))
    out = _run_node(tmp_path, "set_profile.js", script)

    assert "reasoning" in out["calls"], (
        "смена модели шага не перерисовала уровни «Ум»: %r" % (out["calls"],))
    assert "profiles" in out["calls"], out["calls"]
    assert out["profile"] == "Второй", out


# --------------------------------------------------------------------------- #
# 2. Пути клипов: полный путь с сервера, сравнение нормализованное
# --------------------------------------------------------------------------- #
# Общий стенд: боевые clipPathFix/clipKey/clipByXml (40-queue.js) и боевой
# normInsPath (85-inserts-view.js) — правила пути не дублируются в тесте.
_CLIP_PRELUDE = """
function t(s,vars){return String(s).replace(/\\{(\\w+)\\}/g,(m,k)=>String((vars||{})[k]));}
__NORMINS__
__FIX__
__KEY__
__BYXML__
__ADOPT__
"""


def _clip_stand(body: str) -> str:
    prelude = _CLIP_PRELUDE
    prelude = prelude.replace("__NORMINS__", _fn(VIEW_JS, "normInsPath"))
    prelude = prelude.replace("__FIX__", _fn(QUEUE_JS, "clipPathFix"))
    prelude = prelude.replace("__KEY__", _fn(QUEUE_JS, "clipKey"))
    prelude = prelude.replace("__BYXML__", _fn(QUEUE_JS, "clipByXml"))
    prelude = prelude.replace("__ADOPT__", _fn(QUEUE_JS, "cutAdopt"))
    return prelude + "\nconst out={};\n" + body + _TAIL


@node
def test_clip_by_xml_matches_paths_across_separators(tmp_path: Any) -> None:
    """Один файл, записанный разными разделителями, — ОДИН клип, а не второй.

    Полное равенство строк молча плодило дубль клипа со своей разметкой
    (а собирался бы только один).
    """
    out = _run_node(tmp_path, "clip_byxml.js", _clip_stand("""
let CLIPS=[{xml:'C:\\\\out\\\\01_a.xml',name:'01_a.xml'}];
out.forward=!!clipByXml('C:/out/01_a.xml');
out.case=!!clipByXml('c:/OUT/01_a.xml');
out.other=!!clipByXml('C:/out/02_b.xml');
out.empty=!!clipByXml('');
"""))
    assert out["forward"] is True, "путь с другими разделителями не нашёл клип"
    assert out["case"] is True, "регистр пути не учтён нормализацией"
    assert out["other"] is False, "чужой файл принят за тот же клип"
    assert out["empty"] is False, out


@node
def test_saved_windows_style_path_is_repaired_on_linux(tmp_path: Any) -> None:
    """Порт posix-пути плюс обратный слэш на Linux — одно имя файла; чиним на '/'.

    Такую запись оставляла прежняя склейка в `cutAdopt`, и она переживает
    перезагрузку в localStorage: путь чинится при загрузке состояния (applyState).
    """
    out = _run_node(tmp_path, "clip_fix.js", _clip_stand("""
out.linux=clipPathFix('/home/maxi/out\\\\03_C1584.xml');
out.win=clipPathFix('C:\\\\out\\\\03_C1584.xml');
out.plain='';
out.key=clipKey('/home/maxi/out\\\\03_C1584.xml');
"""))
    assert out["linux"] == "/home/maxi/out/03_C1584.xml", out["linux"]
    assert out["win"] == "C:\\out\\03_C1584.xml", "Windows-путь не тронут: %r" % out["win"]
    # ключ сравнения — как у normInsPath: обратные слэши и нижний регистр
    assert out["key"] == "\\home\\maxi\\out\\03_c1584.xml", out["key"]


@node
def test_cut_adopt_takes_full_path_from_server(tmp_path: Any) -> None:
    """`cutAdopt` НЕ подставляет папку сам: `/api/status` отдаёт полные пути.

    Мутация «вернуть склейку od+'\\\\'+x» валит проверку: путь становится
    `<папка>\\<папка>\\имя.xml` (а на Linux — именем файла с '\\' внутри).
    """
    out = _run_node(tmp_path, "cut_adopt.js", _clip_stand("""
let CLIPS=[];
const log=[];
function val(id){return 'C:\\\\out';}
function uiLog(m){log.push(m);}
function saveState(){}function renderClips1(){}function refreshStatuses(){}
function newClip(x){return {xml:x,name:String(x).replace(/^.*[\\\\\\/]/,'')};}
out.added=cutAdopt({results:['C:\\\\out\\\\01_a.xml']});
out.xml=CLIPS.length?CLIPS[0].xml:'';
out.name=CLIPS.length?CLIPS[0].name:'';
// второй опрос того же файла, но с '/' — дубля быть не должно
out.again=cutAdopt({results:['C:/out/01_a.xml']});
out.count=CLIPS.length;
out.log=log;
"""))
    assert out["xml"] == "C:\\out\\01_a.xml", (
        "cutAdopt склеил путь сам: %r" % out["xml"])
    assert out["added"] == 1 and out["count"] == 1, out
    assert out["again"] == 0, "повторный опрос добавил второй клип на тот же файл"
    # в лог идёт ИМЯ, а не простыня пути
    assert out["log"] and "нарезан: 01_a.xml" in out["log"][0], out["log"]


def test_boot_repairs_saved_clip_paths() -> None:
    """Загрузка состояния чинит сохранённые пути тем же clipPathFix.

    Проверяется по исходнику: applyState обязан прогнать путь через общий
    помощник — своя копия правила тут разошлась бы с `clipByXml`.
    """
    boot = _read(BOOT_JS)
    body = boot[boot.index("function applyState(s)"):boot.index("function restoreState()")]
    assert "clipPathFix(c.xml)" in body, (
        "applyState не чинит сохранённый путь клипа (см. clipPathFix в 40-queue.js)")
    assert "function clipPathFix" in _read(QUEUE_JS), "clipPathFix пропал из 40-queue.js"


# --------------------------------------------------------------------------- #
# 3. Полные пути в /api/status.results и имя движка в подписи шага
# --------------------------------------------------------------------------- #
_FAKE_CHILD = textwrap.dedent("""\
import sys
args = sys.argv[1:]
out_path = None
for i, a in enumerate(args):
    if a == "--out" and i + 1 < len(args):
        out_path = args[i + 1]
        break
if out_path:
    with open(out_path, "w") as f:
        f.write("<xml/>")
print("hello from fake omni_cut", flush=True)
""")


def test_results_are_full_paths_and_label_names_the_engine(
        monkeypatch: pytest.MonkeyPatch, tmp_path: "pytest.TempPathFactory") -> None:
    """Один прогон нарезки: в `results` — ПОЛНЫЙ путь, в подписи шага — движок.

    Подпись всегда называла «Omni», даже когда режет GigaAM (движок берётся из
    `active_cut_asr`), а `results` отдавал имя файла — клиент склеивал папку сам.
    """
    import api.jobs as jobs
    from api._core import JOB, LOCK
    from core import aicut

    script_path = str(tmp_path / "fake_omni.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(_FAKE_CHILD)

    monkeypatch.setattr(jobs, "module_cmd",
                        lambda mod, *args: [sys.executable, script_path] + list(args))
    monkeypatch.setattr(jobs, "items_init", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_set", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "item_fail", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "job_finish", lambda: None)
    monkeypatch.setattr(jobs, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "set_stalled", lambda *a, **k: None)
    monkeypatch.setattr(aicut, "cut_parallel_width", lambda m, r, n: 1)
    monkeypatch.setattr(aicut, "cut_asr_engine", lambda **kw: "gigaam")

    lines: list[str] = []

    def capture_emit(line: str = "", /, **kw: Any) -> None:
        lines.append(line.format(**kw) if kw else line)

    monkeypatch.setattr(jobs, "emit", capture_emit)

    with LOCK:
        JOB["cancel"] = False
        JOB["running"] = True
        JOB["done"] = False
        JOB["results"] = []
        JOB["failed"] = []
        JOB["items"] = []

    outdir = str(tmp_path / "out")
    jobs.run_omnicut_job(outdir, [[str(tmp_path / "cam_0.mp4")]], stages={"draft": False})

    with LOCK:
        results = list(JOB["results"])

    assert len(results) == 1, results
    assert results[0] == os.path.join(outdir, "01_cam_0.xml"), (
        "в results не полный путь: %r" % results[0])
    assert os.path.isabs(results[0]), results[0]

    label = [x for x in lines if x.startswith("[1/1]")]
    assert label, "подписи шага нарезки нет в логе: %r" % lines
    assert "GigaAM" in label[0], "подпись шага не называет движок ASR: %r" % label[0]
    assert "Omni" not in label[0], label[0]


def test_cut_engine_label_is_short_engine_name() -> None:
    """Имя движка для подписи — короткое: уточнения каталога в скобках отброшены.

    Строка и так в скобках («ИИ-нарезка (… + LLM + SSM)») — вложенные читались бы
    как мусор, но неизвестный движок отдаём как есть, а не пустотой.
    """
    import api.jobs as jobs

    assert jobs.cut_engine_label("gigaam") == "GigaAM-v3"
    assert jobs.cut_engine_label("omni") == "Omni"
    assert jobs.cut_engine_label("нет-такого") == "нет-такого"


# --------------------------------------------------------------------------- #
# 4. Отказ «уже идёт»: имя задачи-владельца и её прогресс
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client() -> Any:
    from flask import Flask
    import api
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_busy_refusal_names_the_lock_owner(client: Any, tmp_path: Any,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Лок держит сборка прокси превью — отказ нарезки называет её и её прогресс.

    Раньше было «Уже выполняется» без имени задачи и без прогресса: непонятно ни
    что идёт, ни где смотреть.
    """
    from api import _core, previewproxy
    from core import jobstate

    monkeypatch.setattr(jobstate, "JOB_LOCK_PATH", str(tmp_path / "job.lock"))
    monkeypatch.setattr(jobstate, "_JOB_LOCK_FH", None)
    monkeypatch.setattr(jobstate, "_JOB_LOCK_TASK", None)
    monkeypatch.setattr(jobstate, "_JOB_LOCK_HINT", None)
    monkeypatch.setattr(jobstate, "_PENDING_LOCK_TASK", None)
    monkeypatch.setattr(jobstate, "_PENDING_LOCK_HINT", None)

    cam = tmp_path / "cam.mp4"
    cam.write_bytes(b"0")

    with previewproxy.PXLOCK:
        previewproxy.PXJOB.update(running=True, i=2, n=2, pct=40, cur="cam2.mp4")
    # Так лок занимает боевой сборщик прокси (api/previewproxy.py).
    previewproxy.cross_lock_task(previewproxy.PROXY_TASK, previewproxy._proxy_hint)
    assert jobstate._cross_lock_acquire() is True
    try:
        r = client.post("/api/omnicut_run", json={
            "outdir": str(tmp_path / "out"),
            "camdirs": [str(tmp_path)],
            "pairs": [["cam.mp4"]]})
        d = r.get_json()
        # Имя владельца лежит ещё и в файле лока: без своей памяти (вторая копия
        # интерфейса) отказ всё равно назовёт задачу, а не «другую задачу».
        saved = jobstate._JOB_LOCK_TASK
        jobstate._JOB_LOCK_TASK = None
        try:
            file_owner = jobstate.cross_lock_owner()
        finally:
            jobstate._JOB_LOCK_TASK = saved
    finally:
        jobstate._cross_lock_release()
        with previewproxy.PXLOCK:
            previewproxy.PXJOB.update(running=False, i=0, n=0, pct=0, cur="")

    assert d.get("err") == "busy", d
    assert "сборка прокси превью" in d["error"], (
        "отказ не назвал задачу-владельца лока: %r" % d["error"])
    assert "камера 2 из 2" in d["error"], (
        "отказ не назвал прогресс сборки: %r" % d["error"])
    assert "40" in d["error"], d["error"]
    with _core.LOCK:
        assert _core.JOB["running"] is False, "отказ оставил JOB занятым"
    assert file_owner == previewproxy.PROXY_TASK, (
        "имя владельца не доехало до второй копии интерфейса через файл лока: %r"
        % (file_owner,))


def test_busy_refusal_without_owner_keeps_the_old_text(
        client: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Владельца не знаем (лок занят без имени) — прежний текст, без выдумок."""
    from api import previewproxy
    from core import jobstate

    monkeypatch.setattr(jobstate, "JOB_LOCK_PATH", str(tmp_path / "job.lock"))
    monkeypatch.setattr(jobstate, "_JOB_LOCK_FH", None)
    monkeypatch.setattr(jobstate, "_JOB_LOCK_TASK", None)
    monkeypatch.setattr(jobstate, "_JOB_LOCK_HINT", None)

    cam = tmp_path / "cam.mp4"
    cam.write_bytes(b"0")

    assert jobstate._cross_lock_acquire() is True      # без имени задачи
    try:
        d = client.post("/api/omnicut_run", json={
            "outdir": str(tmp_path / "out"),
            "camdirs": [str(tmp_path)],
            "pairs": [["cam.mp4"]]}).get_json()
    finally:
        jobstate._cross_lock_release()
        with previewproxy.PXLOCK:
            previewproxy.PXJOB["running"] = False

    assert d.get("err") == "busy", d
    assert d["error"] == "Уже выполняется", d["error"]


def test_proxy_log_lines_reach_the_common_log(client: Any) -> None:
    """Строки сборщика прокси видны в общей панели «Логи» (/api/status).

    Сообщение отказа посылало человека «смотри Логи», а сборщик писал только в
    свой PXJOB — в панели было пусто. Запись едет тем же видом, что у общего лога:
    шаблон строки плюс переменные (их подставляет интерфейс).
    """
    from api import previewproxy

    previewproxy._emit("превью-прокси {cur}/{total}: {name}",
                       cur=1, total=1, name="cam1.mp4")

    log = client.get("/api/status").get_json()["log"]
    assert {"t": "превью-прокси {cur}/{total}: {name}",
            "v": {"cur": 1, "total": 1, "name": "cam1.mp4"}} in log, (
        "строка сборщика прокси не доехала до общей панели «Логи»: %r" % log[-5:])


# --------------------------------------------------------------------------- #
# 5. doctor: настоящая причина отказа и версии Python из CI
# --------------------------------------------------------------------------- #
def _ci_python_versions() -> set[str]:
    """Версии Python, которые прогоняет CI: литералы `python-version:` в ci.yml."""
    src = _read(CI_YML)
    out: set[str] = set()
    for m in re.finditer(r"python-version:\s*(.+)", src):
        out.update(re.findall(r"[\"'](\d+\.\d+)[\"']", m.group(1)))
    return out


def test_doctor_python_set_matches_ci() -> None:
    """Набор «проверяемых» версий Python в doctor — ровно тот, что в ci.yml.

    Список живёт в коде, матрица — в workflow: без сверки они разъезжаются молча,
    и doctor либо пугает версией, которую CI проверяет, либо молчит о чужой.
    """
    import doctor

    ci = _ci_python_versions()
    assert "3.12" in ci, "в ci.yml не нашлась матрица версий: %r" % ci
    assert set(doctor.PYTHONS_CHECKED) == ci, (
        "набор версий doctor разошёлся с ci.yml: %r против %r"
        % (sorted(doctor.PYTHONS_CHECKED), sorted(ci)))


def _python_row(monkeypatch: pytest.MonkeyPatch, version: tuple[int, int, int]) -> Any:
    """Строка «Python» из check_core при заданной версии интерпретатора."""
    import doctor

    vinfo = collections.namedtuple("version_info", "major minor micro releaselevel serial")
    monkeypatch.setattr(doctor.sys, "version_info", vinfo(*version, "final", 0))
    monkeypatch.setattr(doctor, "_which", lambda name: None)
    monkeypatch.setattr(doctor, "_mod", lambda name: (True, "1.0"))
    doctor._rows = []
    doctor._bad = 0
    doctor.check_core()
    rows = [r for r in doctor._rows if r[1] == "Python"]
    assert rows, "doctor не сказал про версию Python"
    return rows[0]


def test_doctor_does_not_warn_on_a_ci_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """3.12 (из матрицы smoke) — не предупреждение: проект сам её проверяет."""
    import doctor

    status, _what, detail = _python_row(monkeypatch, (3, 12, 3))
    assert status == doctor.OK, "doctor пугает версией, которую проверяет CI: %r" % detail
    assert "3.12.3" in detail, detail


def test_doctor_warns_outside_the_checked_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Версия вне набора CI — предупреждение остаётся (колёс под неё может не быть)."""
    import doctor

    status, _what, detail = _python_row(monkeypatch, (3, 15, 0))
    assert status == doctor.WARN, "нет предупреждения о версии вне набора CI: %r" % detail


def test_doctor_optional_failure_shows_exception_type(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """«нет (RuntimeError) — отключится: …»: тип исключения отличает «нет пакета»
    от «пакет стоит, но падает на импорте» (rembg с неработающим numba)."""
    import doctor

    monkeypatch.setattr(doctor, "_mod", lambda name: (False, "RuntimeError"))
    doctor._rows = []
    doctor._bad = 0
    doctor.check_optional()
    rows = [r for r in doctor._rows if r[1] == "rembg"]
    assert rows, "строки про rembg нет: %r" % doctor._rows[:5]
    status, _what, detail = rows[0]
    assert status == doctor.WARN, rows[0]
    assert "RuntimeError" in detail, (
        "в строке отказа нет типа исключения: %r" % detail)
    # Точный текст — через t(): тест не должен зависеть от языка интерфейса.
    assert detail == doctor.t("нет ({info}) — отключится: {feature}", info="RuntimeError",
                              feature=doctor.t("снятие фона у сгенерированных картинок")), detail
