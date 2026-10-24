# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""Память картинки вставки за файлом базы: remember_look / look_of, слияние и рескан
индекса, отдача через match_many и find_named, контракт /api/insert_memo.

Память — маска (mw/mh), масштаб (sc), точка покоя (x/y) и снятый фон (nobg). Пишется в
запись items[] индекса, поэтому подобранное один раз приезжает в следующий ролик. Файла
нет в базе — память некуда записать, это не ошибка (ответ saved=false).

Индекс свой, в tmp_path: INDEX_PATH подменён, кэш пуст, _seed_index не копирует боевой
insertlib.json, эмбеддер отключён — сети и моделей тут нет.

Запуск:  py -3.10 -m pytest tests/test_insert_memo.py -q -p no:cacheprovider
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("REELSI_NO_BROWSER", "1")

from core import insertlib  # noqa: E402
import api  # noqa: E402

H = {"Host": "127.0.0.1:5001"}

EMPTY_CACHE = {"mtime": 0, "data": None, "mat": None, "mat_items": None,
               "df": None, "cand_words": None, "N": 0}


@pytest.fixture
def index(tmp_path, monkeypatch):
    """Свой индекс вставок в tmp_path; боевой insertlib.json не читается и не пишется."""
    idx = str(tmp_path / "insertlib.json")
    monkeypatch.setattr(insertlib, "INDEX_PATH", idx)
    monkeypatch.setattr(insertlib, "_CACHE", dict(EMPTY_CACHE))
    monkeypatch.setattr(insertlib, "_seed_index", lambda: None)
    monkeypatch.setattr(insertlib, "_emb_model", lambda: None)
    return idx


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    app.config["TESTING"] = True
    return app.test_client()


def _png(path, w=40, h=30):
    from PIL import Image
    Image.new("RGBA", (w, h), (200, 30, 30, 255)).save(str(path))
    return str(path)


def _write(idx, items):
    with open(idx, "w", encoding="utf-8") as f:
        json.dump({"dirs": [], "emb_model": "", "emb_tag": insertlib.EMB_TAG, "items": items},
                  f, ensure_ascii=False)


def _read(idx):
    with open(idx, encoding="utf-8") as f:
        return json.load(f)


def _entry(path, **extra):
    it = {"path": str(path), "name": os.path.basename(str(path)), "type": "photo",
          "used": 0, "desc": "broken glasses", "desc_src": "user", "emb": None}
    it.update(extra)
    return it


def test_remember_look_writes_all_fields_to_index(index, tmp_path):
    """Все шесть полей памяти пишутся в запись и читаются обратно — и из кэша, и из файла."""
    p = _png(tmp_path / "glasses.png")
    _write(index, [_entry(p)])
    fields = {"mw": 137, "mh": 90, "sc": 120, "x": -15, "y": 30.5, "nobg": True}

    assert insertlib.remember_look(p, fields) is True
    assert insertlib.look_of(p) == fields
    stored = _read(index)["items"][0]
    assert {k: stored[k] for k in fields} == fields, "поля не легли в файл индекса"


def test_remember_look_default_values_remove_fields(index, tmp_path):
    """100/100 маски, масштаб 100 и сдвиг 0 — «как считает JSX сам», поля из записи уходят.
    Поле nobg, которого нет в наборе, не трогается."""
    p = _png(tmp_path / "a.png")
    _write(index, [_entry(p, mw=137, mh=90, sc=120, x=5, y=-5, nobg=True)])

    assert insertlib.remember_look(p, {"mw": 100, "mh": 100, "sc": 100, "x": 0, "y": 0})
    assert insertlib.look_of(p) == {"nobg": True}
    stored = _read(index)["items"][0]
    assert not any(k in stored for k in ("mw", "mh", "sc", "x", "y"))


def test_remember_look_stores_nobg_false(index, tmp_path):
    """nobg=False — тоже память («фон у этой картинки оставить»), а не пустое поле."""
    p = _png(tmp_path / "b.png")
    _write(index, [_entry(p, nobg=True)])

    insertlib.remember_look(p, {"nobg": False})
    assert insertlib.look_of(p) == {"nobg": False}


def test_remember_look_ignores_garbage(index, tmp_path):
    """Битые значения пропускаются молча: строка, bool, NaN, выход за пределы, чужие ключи.
    Запомненное раньше не портится, ошибки нет — ответ True, запись найдена."""
    p = _png(tmp_path / "c.png")
    _write(index, [_entry(p, sc=150, x=7)])

    garbage = {
        "mw": "abc", "mh": 137,            # строка в mw — пары нет, маска не меняется
        "sc": 9999,                        # вне 10..400
        "x": float("nan"),                 # не конечное
        "y": True,                         # bool — не число
        "nobg": "yes",                     # не bool
        "чужой": 1,
    }
    assert insertlib.remember_look(p, garbage) is True
    assert insertlib.look_of(p) == {"sc": 150, "x": 7}

    # маска вне 20..1000 и сдвиг за 10000 px — тоже мусор
    assert insertlib.remember_look(p, {"mw": 1500, "mh": 137, "x": 20000}) is True
    assert insertlib.look_of(p) == {"sc": 150, "x": 7}


def test_remember_look_false_for_file_outside_index(index, tmp_path):
    """Картинки нет в базе — False, индекс не тронут: память придёт при сборке."""
    p_in = _png(tmp_path / "in.png")
    p_out = _png(tmp_path / "stranger.png")
    _write(index, [_entry(p_in)])
    before = open(index, encoding="utf-8").read()

    assert insertlib.remember_look(p_out, {"sc": 120}) is False
    assert insertlib.look_of(p_out) == {}
    assert open(index, encoding="utf-8").read() == before


def test_rescan_keeps_look_fields(index, tmp_path):
    """Полный рескан не стирает память: маска, масштаб, точка покоя и nobg=False (ложное
    значение — тоже память; проверка на истинность её бы потеряла)."""
    d = tmp_path / "lib"
    d.mkdir()
    p = _png(d / "glasses.png")
    memo = {"mw": 137, "mh": 90, "sc": 120, "x": -15, "y": 30, "nobg": False}
    _write(index, [_entry(p, **memo)])

    insertlib.build_index([str(d)], use_emb=False)

    assert insertlib.look_of(p) == memo


def test_merge_prev_records_keeps_look_fields():
    """Слияние дублей одного пути: память берётся из той записи, где она есть, и nobg=False
    из второй записи не теряется."""
    a = {"path": "x.png", "desc": "d", "desc_src": "user", "sc": 120, "nobg": False}
    b = {"path": "x.png", "desc": "", "desc_src": "name", "x": -4, "y": 7}
    m = insertlib._merge_prev_records([a, b])
    assert (m["sc"], m["nobg"], m["x"], m["y"]) == (120, False, -4, 7)


def test_match_many_and_find_named_return_look_fields(index, tmp_path):
    """Подбор отдаёт запомненную память рядом с путём: картинка приедет в ролик уже
    настроенной, а не подгоняется заново."""
    p = _png(tmp_path / "broken glasses.png")
    _write(index, [_entry(p, mw=137, mh=90, sc=120, x=-15, y=30, nobg=True)])

    got = insertlib.match_many(["broken glasses"], k=3)[0]
    assert got and got[0]["path"] == p
    assert (got[0]["mw"], got[0]["mh"], got[0]["sc"], got[0]["x"], got[0]["y"],
            got[0]["nobg"]) == (137, 90, 120, -15, 30, True)

    hit = insertlib.find_named(["glasses"], kind="photo")
    assert hit is not None and hit["path"] == p
    assert (hit["mw"], hit["mh"], hit["sc"], hit["x"], hit["y"], hit["nobg"]) == \
        (137, 90, 120, -15, 30, True)


def test_insert_memo_get_empty_without_record(index, client, tmp_path):
    """GET по файлу, которого нет в базе, — ok и пустая память, не ошибка."""
    p = _png(tmp_path / "none.png")
    _write(index, [])
    r = client.get("/api/insert_memo", query_string={"path": p}, headers=H)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "memo": {}}
    r = client.get("/api/insert_memo", headers=H)
    assert r.get_json() == {"ok": True, "memo": {}}


def test_insert_memo_post_then_get_roundtrip(index, client, tmp_path):
    """POST пишет память и отвечает saved=true; GET отдаёт её обратно."""
    p = _png(tmp_path / "roundtrip.png")
    _write(index, [_entry(p)])
    body = {"path": p, "mw": 137, "mh": 90, "sc": 120, "x": -15, "y": 30, "nobg": True}

    r = client.post("/api/insert_memo", json=body, headers=H)
    assert r.get_json() == {"ok": True, "saved": True}

    r = client.get("/api/insert_memo", query_string={"path": p}, headers=H)
    assert r.get_json()["memo"] == {"mw": 137, "mh": 90, "sc": 120, "x": -15, "y": 30,
                                    "nobg": True}


def test_insert_memo_post_outside_index_and_without_path(index, client, tmp_path):
    """Файла нет в базе или path пустой — saved=false при ok=true: не ошибка."""
    p_in = _png(tmp_path / "in.png")
    p_out = _png(tmp_path / "out.png")
    _write(index, [_entry(p_in)])

    r = client.post("/api/insert_memo", json={"path": p_out, "sc": 120}, headers=H)
    assert r.get_json() == {"ok": True, "saved": False}
    r = client.post("/api/insert_memo", json={"sc": 120}, headers=H)
    assert r.get_json() == {"ok": True, "saved": False}
    assert insertlib.look_of(p_in) == {}


def test_build_moves_file_into_base_with_full_memory(index, tmp_path):
    """Первая сборка: файл вне базы переезжает в базу, и ВМЕСТЕ с ним уходит вся память
    картинки (масштаб, точка покоя, «без фона» — не только маска). Мутация «adopt пишет
    только mw/mh» (см. _memo_of) — красная."""
    from api import inserts as api_inserts
    base = tmp_path / "insert_library"
    base.mkdir()
    _write(index, [])
    down = tmp_path / "Downloads"
    down.mkdir()
    src = _png(down / "glasses.png")
    inserts = [{"media": src, "query": "broken glasses", "prompt": "",
                "mw": 137, "mh": 90, "sc": 70, "x": 12, "y": 0, "nobg": True}]

    api_inserts._adopt_inserts(inserts, str(base))

    new = inserts[0]["media"]
    assert new != src and os.path.isfile(new), "файл не переехал в базу"
    assert insertlib.look_of(new) == {"mw": 137, "mh": 90, "sc": 70, "x": 12, "nobg": True}


def test_build_on_file_already_in_base_also_writes_memo(index, tmp_path):
    """Файл уже лежит в базе — не двигаем, но память этой сборки всё равно пишется поверх
    прошлой (юзер только что видел её в предпросмотре)."""
    from api import inserts as api_inserts
    base = tmp_path / "insert_library"
    (base / "photos").mkdir(parents=True)
    pic = _png(base / "photos" / "mug.png")
    _write(index, [_entry(pic, sc=150)])
    inserts = [{"media": pic, "query": "coffee mug", "sc": 70, "x": 12, "nobg": False}]

    api_inserts._adopt_inserts(inserts, str(base))

    assert inserts[0]["media"] == pic
    assert insertlib.look_of(pic) == {"sc": 70, "x": 12, "nobg": False}


# ---- фронт: дверь привязки файла и «Фон долой» — боевые функции под node -------------
# Тела вырезаются из static/app/*.js по балансу скобок (приём tests/test_plate_fix.py),
# внешние двери — заглушки. Копий правил в тесте нет.

INSERTS_JS = os.path.join(ROOT, "static", "app", "80-inserts.js")
SETTINGS_JS = os.path.join(ROOT, "static", "app", "10-settings.js")
QUEUE_JS = os.path.join(ROOT, "static", "app", "40-queue.js")
node = pytest.mark.skipif(not shutil.which("node"), reason="контракт фронта требует node в PATH")


def _read_src(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _func(src, name):
    """Тело функции целиком, вместе с `async` (без него `await` внутри не разбирается)."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src)
    assert m, f"в исходнике не нашлась функция {name}"
    i = src.index("{", m.end() - 1)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
    raise AssertionError(f"не сошлись скобки у {name}")


# Заглушки окружения. fetch отдаёт память из MEMO по пути из query; запросы пишутся в CALLS.
FRONT_STUBS = r"""
const CALLS=[];let MEMO={};let SPEAKERS={};let CLIPS=[];let curIns=0;let SAVED=[];
function t(s){return s;}
function uiLog(){}
function isPhotoPath(p){return /\.(png|jpe?g|webp|gif|bmp|avif|tiff?)$/i.test(String(p||''));}
function renderInsHost(){}
function syncClipLists(){}
function saveState(){}
function ipvRefresh(){}
function insMemoSave(x){SAVED.push(x.nobg);}
function fetch(url){
  CALLS.push(url);
  const m=/[?&]path=([^&]*)/.exec(url);
  const memo=m?(MEMO[decodeURIComponent(m[1])]||{}):{};
  return Promise.resolve({json:()=>Promise.resolve({ok:true,memo:memo})});
}
const tick=()=>new Promise(r=>setTimeout(r,0));
"""


def _run_front(body, funcs_from):
    """Стенд + вырезанные функции + сценарий; сценарий печатает JSON последней строкой."""
    code = FRONT_STUBS
    for path, group in funcs_from:
        src = _read_src(path)
        code += "\n".join(_func(src, n) for n in group) + "\n"
    code += body
    p = subprocess.run(["node", "-e", code], capture_output=True, text=True,
                       encoding="utf-8-sig", errors="replace", timeout=60)
    assert p.returncode == 0, (p.stderr or p.stdout).strip()[:900]
    return json.loads(p.stdout.strip().splitlines()[-1])


DOOR_FUNCS = ("insKind", "insSetMedia", "insSpeakerNobg", "insApplyMemo", "insMemoFetch")


@node
def test_media_door_memo_beats_speaker_and_speaker_fills_the_rest(tmp_path):
    """7. Дверь привязки файла: память картинки главнее спикера, спикер — только умолчание.

    Спикер А хочет «без фона», спикер Б — нет. Картинка с памятью nobg=false в базе остаётся
    без фона, даже у спикера А; без памяти — как хочет спикер клипа; память, пришедшая уже
    после привязки, перекрывает умолчание; ответ, пришедший к другой картинке, не
    применяется; видео памяти и флага не получает — ни запроса, ни поля.
    """
    body = r"""
(async()=>{
  const out={};
  SPEAKERS['СпикерА']={ins_nobg:true}; SPEAKERS['СпикерБ']={};
  const cA={job:{speaker:'СпикерА'}}, cB={job:{speaker:'СпикерБ'}};
  const P='C:/lib/photo.png';
  // 1. память пришла с привязкой (выбор из базы): nobg=false и sc главнее спикера А
  const x1={media:'',type:'photo'};
  insSetMedia(x1,P,cA,{nobg:false,sc:150});
  out.memo_wins={nobg:x1.nobg,sc:x1.sc,fetches:CALLS.length};
  // 2. без памяти: умолчание спикера клипа — у А «да», у Б «нет»
  const x2={media:'',type:'photo'}; insSetMedia(x2,P,cA);
  const x3={media:'',type:'photo'}; insSetMedia(x3,P,cB);
  out.speaker_default={a:x2.nobg,b:x3.nobg};
  // 3. память приходит ПОСЛЕ привязки: сразу — умолчание А, потом ответ базы (nobg=false)
  MEMO[P]={nobg:false};
  CALLS.length=0;
  const x4={media:'',type:'photo'}; insSetMedia(x4,P,cA);
  out.before_answer=x4.nobg;
  await tick(); await tick();
  out.after_answer={nobg:x4.nobg,fetches:CALLS.length};
  // 4. ответ к другой картинке не применяем: за время запроса выбрали другой файл
  MEMO[P]={sc:200};
  const x5={media:'',type:'photo'}; insSetMedia(x5,P,cA); x5.media='C:/lib/other.png';
  await tick(); await tick();
  out.stale_sc=(x5.sc===undefined)?null:x5.sc;
  // 6. новая картинка на карточке с «без фона» от прежнего файла: флаг выставляется заново —
  //    у спикера В галки нет и памяти у новой картинки нет → false, не наследуется
  SPEAKERS['СпикерВ']={};
  const cV={job:{speaker:'СпикерВ'}};
  const x6={media:'C:/lib/old.png',type:'photo',nobg:true};
  MEMO['C:/lib/new.png']={};
  insSetMedia(x6,'C:/lib/new.png',cV);
  out.inherit={nobg:x6.nobg};
  // 6б. у новой картинки в памяти «без фона» есть — память главнее прежнего флага
  const x7={media:'C:/lib/old.png',type:'photo',nobg:false};
  insSetMedia(x7,'C:/lib/new2.png',cA,{mw:100,mh:100,sc:100,x:0,y:0,nobg:true});
  out.memo_nobg={nobg:x7.nobg};
  // 5. видео: ни запроса памяти, ни флага «без фона»
  CALLS.length=0;
  const v={media:'',type:'video'}; insSetMedia(v,'C:/lib/clip.mp4',cA);
  await tick();
  out.video={nobg:(v.nobg===undefined)?null:v.nobg,fetches:CALLS.length};
  console.log(JSON.stringify(out));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""
    res = _run_front(body, ((INSERTS_JS, DOOR_FUNCS), (QUEUE_JS, ("clipSpeaker",))))
    assert res["memo_wins"] == {"nobg": False, "sc": 150, "fetches": 0}, res["memo_wins"]
    assert res["speaker_default"] == {"a": True, "b": False}, res["speaker_default"]
    assert res["before_answer"] is True, "умолчание спикера не встало сразу при привязке"
    assert res["after_answer"]["nobg"] is False and res["after_answer"]["fetches"] == 1, res
    assert res["stale_sc"] is None, "ответ памяти применился к другой картинке"
    assert res["inherit"] == {"nobg": False}, "«без фона» унаследовалось от прежней картинки"
    assert res["memo_nobg"] == {"nobg": True}, res["memo_nobg"]
    assert res["video"] == {"nobg": None, "fetches": 0}, res["video"]


@node
def test_ai_named_insert_gets_its_memory_through_the_door(tmp_path):
    """10. Ответ /api/ai_inserts: вставка по названию получает память картинки и «без фона»
    спикера ДВЕРЬЮ — функцией insFromAI, которая зовёт insSetMedia (второй копии правил нет).

    Память, пришедшая с сервером (sc, x, nobg), встаёт сразу и без запроса в сеть. Пустая
    память — умолчание спикера КЛИПА. ИИ-вставка картинки не получает, memo в вставке не
    остаётся.
    """
    body = r"""
(async()=>{
  const out={};
  SPEAKERS['СпикерА']={ins_nobg:true}; SPEAKERS['СпикерБ']={};
  const P='C:/lib/coffee.png';
  const reply=()=>({inserts:[
    {auto:'named',type:'photo',start_sec:34.2,media:P,memo:{sc:70,x:12,nobg:true}},
    {auto:'named',type:'photo',start_sec:50,media:P,memo:{}},
    {auto:'ai',type:'photo',start_sec:70,media:'C:/lib/ignored.png',memo:{sc:90}},
    {auto:'named',type:'photo',start_sec:90,media:'',memo:{sc:55}}
  ]});
  const cA={job:{speaker:'СпикерА'}}, cB={job:{speaker:'СпикерБ'}};
  insFromAI(cA,reply());
  out.mem={sc:cA.inserts[0].sc,x:cA.inserts[0].x,nobg:cA.inserts[0].nobg,media:cA.inserts[0].media};
  out.empty_memo_a=cA.inserts[1].nobg;
  insFromAI(cB,reply());
  out.empty_memo_b=cB.inserts[1].nobg;
  out.ai_media=cA.inserts[2].media;
  out.ai_has_memo=('memo' in cA.inserts[2]);
  out.ai_nobg=(cA.inserts[2].nobg===undefined)?null:cA.inserts[2].nobg;
  out.memo_left=cA.inserts.some(x=>'memo' in x);
  out.no_picture_sc=(cA.inserts[3].sc===undefined)?null:cA.inserts[3].sc;
  out.fetches=CALLS.length;
  console.log(JSON.stringify(out));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""
    res = _run_front(body, ((INSERTS_JS, DOOR_FUNCS + ("insFromAI",)), (QUEUE_JS, ("clipSpeaker",))))
    assert res["mem"] == {"sc": 70, "x": 12, "nobg": True, "media": "C:/lib/coffee.png"}, res["mem"]
    assert res["empty_memo_a"] is True and res["empty_memo_b"] is False, res
    assert res["ai_media"] == "" and res["ai_has_memo"] is False and res["ai_nobg"] is None, res
    assert res["memo_left"] is False, "поле memo осталось в самой вставке"
    assert res["no_picture_sc"] is None, "память применилась к вставке без картинки"
    assert res["fetches"] == 0, "память пошла в сеть: ответ ИИ уже несёт её"


@node
def test_nobg_toggle_flips_flag_and_never_calls_rembg(tmp_path):
    """9. «Фон долой» переключает флаг вставки и не зовёт /api/rembg: файл в базе не плодится.

    Два нажатия — флаг туда и обратно, каждый раз запись памяти. Видео (кнопки нет) не меняется.
    """
    body = r"""
(async()=>{
  const out={};
  const X={media:'C:/lib/p.png',type:'photo'};
  const V={media:'C:/lib/v.mp4',type:'video'};
  CLIPS=[{xml:'a.xml',inserts:[X,V]}]; curIns=0;
  insRembg(0);
  out.first={nobg:X.nobg,calls:CALLS.slice(),saved:SAVED.slice()};
  insRembg(0);
  out.second={nobg:X.nobg,saved:SAVED.slice()};
  insRembg(1);
  out.video={nobg:(V.nobg===undefined)?null:V.nobg,calls:CALLS.length};
  console.log(JSON.stringify(out));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""
    res = _run_front(body, ((INSERTS_JS, ("insKind",)), (SETTINGS_JS, ("insRembg",))))
    assert res["first"]["nobg"] is True and res["first"]["saved"] == [True], res["first"]
    assert res["first"]["calls"] == [], f"«Фон долой» всё ещё ходит в сеть: {res['first']['calls']}"
    assert res["second"] == {"nobg": False, "saved": [True, False]}, res["second"]
    assert res["video"] == {"nobg": None, "calls": 0}, res["video"]


@node
def test_picked_image_closes_both_choice_rows(tmp_path):
    """11. Выбрал картинку — ряды «База» (📚) и «Стоки» закрываются оба, каким бы способом ни
    выбрали. Закрывает дверь привязки insSetMedia: через неё идут выбор со стока, из базы,
    файлом, генерацией и автоподбор, поэтому отдельных сбросов в каждом способе нет.

    Открыты оба ряда; выбор со стока (insStockPick), из базы (insLibPick), прямой проход через
    дверь (файл, генерация) и видео — после каждого оба ряда закрыты.
    """
    body = r"""
function toast(){}
function val(){return '';}
function errText(d){return String(d&&d.error);}
(async()=>{
  const out={};
  // Сток отдаёт путь уже скачанного файла; ответ памяти пустой.
  fetch=(url,opts)=>Promise.resolve({json:()=>Promise.resolve(
    {ok:true,memo:{},path:'C:/lib/stock/cat-1.jpg'})});
  const rows=x=>({lib:x.libShown,stock:x.stockShown});
  const mk=()=>({media:'',type:'photo',libShown:true,stockShown:true,
    libOpts:[{path:'C:/lib/base/cat.jpg'}],stockOpts:[{provider:'pexels',id:1}]});
  // 1. выбор со стока
  const s=mk(); CLIPS=[{xml:'a.xml',job:{},inserts:[s]}]; curIns=0;
  out.before=rows(s);
  await insStockPick(0,0);
  out.stock_pick={...rows(s),media:s.media};
  // 2. выбор из базы
  const b=mk(); CLIPS=[{xml:'a.xml',job:{},inserts:[b]}]; curIns=0;
  insLibPick(0,0);
  out.lib_pick={...rows(b),media:b.media};
  // 3. файл выбран напрямую через дверь (как выбор файлом и генерация)
  const f=mk(); insSetMedia(f,'C:/lib/file.png',CLIPS[0]);
  out.file_pick=rows(f);
  // 4. видео: дверь закрывает ряды и до ветки «только картинка»
  const v=mk(); v.type='video'; insSetMedia(v,'C:/lib/clip.mp4',CLIPS[0]);
  out.video_pick=rows(v);
  console.log(JSON.stringify(out));
})().catch(e=>{console.error(e&&e.stack||e);process.exit(1);});
"""
    res = _run_front(body, ((INSERTS_JS, DOOR_FUNCS + ("insLibPick", "insStockPick")),
                            (QUEUE_JS, ("clipSpeaker",))))
    assert res["before"] == {"lib": True, "stock": True}, "у карточки должны быть открыты оба ряда"
    assert res["stock_pick"] == {"lib": False, "stock": False,
                                 "media": "C:/lib/stock/cat-1.jpg"}, res["stock_pick"]
    assert res["lib_pick"] == {"lib": False, "stock": False,
                               "media": "C:/lib/base/cat.jpg"}, res["lib_pick"]
    assert res["file_pick"] == {"lib": False, "stock": False}, res["file_pick"]
    assert res["video_pick"] == {"lib": False, "stock": False}, res["video_pick"]
