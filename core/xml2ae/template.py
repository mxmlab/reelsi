# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Maxim Si
"""JS-шаблон сборки проекта в After Effects (AE_FULL).

Отдельный модуль без единой строки Python: это шестьсот строк ExtendScript, который
подставляется данными и уезжает в AE. Держать его посреди логики — значит листать
через него каждый раз, когда правишь сборку.
"""


SUBS_LOOP_WORDS = r"""    for (var i=0;i<SUBS.length;i++){
        var sw=SUBS[i], hl=sw[3], row=(sw[4]|0);
        var L = subc.layers.addText(sw[2]);
        var sp = L.property("ADBE Text Properties").property("ADBE Text Document");
        var d = sp.value; d.resetCharStyle(); d.resetParagraphStyle(); d.text=sw[2];
        try{d.font=(hl?HL_FONT:FONT);}catch(e){ try{d.font=FONT;}catch(e2){} }   // жёлтый шрифт не найден -> база (не дефолт AE)
        try{d.fauxBold=(hl&&HL_BOLD);}catch(e){}   // искусственный жирный на жёлтых
        d.fontSize=FONT_SIZE; d.fillColor=(hl?HL_FILL:FILL); d.applyFill=true;
        try{d.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}
        sp.setValue(d);
        try{ var rr=L.sourceRectAtTime(sw[0]/FPS+0.001,false);
             if(rr.width>FITW){ d.fontSize=Math.max(40, Math.floor(FONT_SIZE*FITW/rr.width)); sp.setValue(d); } }catch(e){}
        var posP = L.property("ADBE Transform Group").property("ADBE Position");
        var t0 = sw[0]/FPS;
        L.inPoint = t0;
        if (hl){
            var finalY = POSY + row*HL_STEP;
            L.outPoint = sw[5]/FPS;             // общий конец стопки — вся связка исчезает разом
            posP.setValueAtTime(t0,        [SW/2, finalY+HL_RISE]);
            posP.setValueAtTime(t0+HL_DUR, [SW/2, finalY]);
            var op = L.property("ADBE Transform Group").property("ADBE Opacity");
            op.setValueAtTime(t0, 0); op.setValueAtTime(t0+HL_DUR, 100);
            easePair(posP); easePair(op);
        } else {
            L.outPoint = sw[1]/FPS;
            posP.setValue([SW/2, POSY]);
        }%(sub_count_code)s
    }"""

SUBS_LOOP_WORDS_JOINED = r"""    var i=0;
    while (i<SUBS.length){
        var sw=SUBS[i], hl=sw[3], row=(sw[4]|0);
        if (!hl){
            var L = subc.layers.addText(sw[2]);
            var sp = L.property("ADBE Text Properties").property("ADBE Text Document");
            var d = sp.value; d.resetCharStyle(); d.resetParagraphStyle(); d.text=sw[2];
            try{d.font=FONT;}catch(e){}
            d.fontSize=FONT_SIZE; d.fillColor=FILL; d.applyFill=true;
            try{d.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}
            sp.setValue(d);
            try{ var rr=L.sourceRectAtTime(sw[0]/FPS+0.001,false);
                 if(rr.width>FITW){ d.fontSize=Math.max(40, Math.floor(FONT_SIZE*FITW/rr.width)); sp.setValue(d); } }catch(e){}
            var posP = L.property("ADBE Transform Group").property("ADBE Position");
            var t0 = sw[0]/FPS;
            L.inPoint = t0;
            L.outPoint = sw[1]/FPS;
            posP.setValue([SW/2, POSY]);%(sub_count_code)s
            i++;
        } else {
            var j=i;
            while (j+1<SUBS.length && SUBS[j+1][3] && ((SUBS[j+1][4]|0)===row) && SUBS[j+1][5]===sw[5]){
                j++;
            }
            var r_layers=[], r_widths=[], r_words=[];
            for (var k=i; k<=j; k++){
                var kw=SUBS[k];
                var L = subc.layers.addText(kw[2]);
                var sp = L.property("ADBE Text Properties").property("ADBE Text Document");
                var d = sp.value; d.resetCharStyle(); d.resetParagraphStyle(); d.text=kw[2];
                try{d.font=HL_FONT;}catch(e){ try{d.font=FONT;}catch(e2){} }
                try{d.fauxBold=HL_BOLD;}catch(e){}
                d.fontSize=FONT_SIZE; d.fillColor=HL_FILL; d.applyFill=true;
                try{d.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}
                sp.setValue(d);
                var rr={width:0};
                try{ rr=L.sourceRectAtTime(kw[0]/FPS+0.001,false);
                     if(rr.width>FITW){ d.fontSize=Math.max(40, Math.floor(FONT_SIZE*FITW/rr.width)); sp.setValue(d);
                                        rr=L.sourceRectAtTime(kw[0]/FPS+0.001,false); } }catch(e){}
                r_layers.push(L);
                r_widths.push(rr.width);
                r_words.push(kw);
            }
            var spc = FONT_SIZE * 0.28;
            var totW = 0;
            for (var k=0; k<r_widths.length; k++) totW += r_widths[k];
            totW += Math.max(0, r_widths.length - 1) * spc;
            var curX = (SW - totW) / 2;
            var finalY = POSY + row*HL_STEP;
            for (var wi=0; wi<r_layers.length; wi++){
                var L = r_layers[wi], sw = r_words[wi];
                var sp = L.property("ADBE Text Properties").property("ADBE Text Document");
                var t0 = sw[0]/FPS;
                L.inPoint = t0;
                L.outPoint = sw[5]/FPS;
                var wCenter = (r_layers.length > 1) ? (curX + r_widths[wi] / 2) : (SW / 2);
                curX += r_widths[wi] + spc;
                var posP = L.property("ADBE Transform Group").property("ADBE Position");
                posP.setValueAtTime(t0,        [wCenter, finalY+HL_RISE]);
                posP.setValueAtTime(t0+HL_DUR, [wCenter, finalY]);
                var op = L.property("ADBE Transform Group").property("ADBE Opacity");
                op.setValueAtTime(t0, 0); op.setValueAtTime(t0+HL_DUR, 100);
                easePair(posP); easePair(op);%(sub_count_code)s
            }
            i = j + 1;
        }
    }"""

SUBS_LOOP_ROWS = r"""    var SUB_ROWS = %(sub_rows)s;
    var SUB_STEP = %(sub_step)g;
    for (var ri=0; ri<SUB_ROWS.length; ri++){
        var sr=SUB_ROWS[ri], r_t0=sr[0]/FPS, r_t1=sr[1]/FPS, r_row=sr[2], r_fsz=sr[3], r_words=sr[4];
        var r_layers=[], r_widths=[];
        var cur_fsz = r_fsz || FONT_SIZE;
        for (var wi=0; wi<r_words.length; wi++){
            var wd=r_words[wi], w_hl=wd[2];
            var L=subc.layers.addText(wd[1]);
            var sp=L.property("ADBE Text Properties").property("ADBE Text Document");
            var d=sp.value; d.resetCharStyle(); d.resetParagraphStyle(); d.text=wd[1];
            try{d.font=(w_hl?HL_FONT:FONT);}catch(e){ try{d.font=FONT;}catch(e2){} }
            try{d.fauxBold=(w_hl&&HL_BOLD);}catch(e){}
            d.fontSize=cur_fsz; d.fillColor=(w_hl?HL_FILL:FILL); d.applyFill=true;
            try{d.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}
            sp.setValue(d);
            L.inPoint = r_t0;
            L.outPoint = r_t1;
            r_layers.push(L);
            var rr={width:0};
            try{ rr=L.sourceRectAtTime(r_t0+0.001, false); }catch(e){}
            r_widths.push(rr.width);
        }
        var spc = cur_fsz * 0.28;
        var totW = 0;
        for (var k=0; k<r_widths.length; k++) totW += r_widths[k];
        totW += Math.max(0, r_widths.length - 1) * spc;
        var curX = (SW - totW) / 2;
        var cur_step = r_fsz ? (cur_fsz * 1.18) : SUB_STEP;
        var lineY = POSY + r_row * cur_step;
        for (var wi=0; wi<r_layers.length; wi++){
            var wCenter = curX + r_widths[wi] / 2;
            r_layers[wi].property("ADBE Transform Group").property("ADBE Position").setValue([wCenter, lineY]);
            curX += r_widths[wi] + spc;
        }
    }"""


AE_FULL = r"""// SPDX-License-Identifier: AGPL-3.0-or-later
// Reelsi -> After Effects FULL build (auto-generated). Run: File>Scripts>Run Script File
(function () {
    // ===== STYLE / SHADOW (edit me) =====
    var FONT = %(font)s;                       // PostScript-имя шрифта базового текста
    var HL_FONT = %(hl_font)s;                 // шрифт выделенных слов (жирный вариант)
    var HL_BOLD = %(hl_bold)s;                 // искусственный жирный (fauxBold) на выделенных
    var FONT_SIZE = %(fsize)d, FILL = %(fill)s, POSY = %(posy)d;
    var HL_FILL = %(hlfill)s%(hlfill3_decl)s;                  // цвет выделения [r,g,b]
    var HL_RISE = %(hl_rise)g, HL_DUR = 0.35;  // slide-up: снизу вверх на HL_RISE px за HL_DUR c
    var HL_STEP = %(hl_step)g;                 // шаг вертикальной стопки для подряд идущих жёлтых
    var HL_EASE_OUT = %(hl_ease_out)d, HL_EASE_IN = %(hl_ease_in)d;     // cubic-bezier(0.35,0.01,0.10,0.99)
    var SH_OPACITY = %(sh_op)g, SH_DIR = %(sh_dir)g, SH_DIST = %(sh_dist)g, SH_SOFT = %(sh_soft)g;%(intro_shadow_decl)s
    // вставки фото/видео — дефолты-средние из компа 1221 (правь при желании)
    var INS_MASK_R = 60;                                  // радиус скругления маски на прекомпе фото, px
    var INS_FX = %(ins_fx)s;                              // "card" чёрная тень+скругление | "white" старый вид (белая тень+чокер)
    var INS_C1_ON2_X = %(ins_c1on2_x)g, INS_C1_ON2_Y = %(ins_c1on2_y)g;  // стиль кам1, попавший на перебивку: общий сдвиг точки покоя всех таких вставок, px
    var INS_C2_Y = %(ins_c2y)d%(ins_c2x_decl)s;                           // Кам2: Y точки покоя вставки, px (считает Python: INS_C2_Y_FR * H)
    var INS_MASK_SQUARE_AR = 2.2;                         // фото уже этого отношения сторон режем маской в квадрат (ультравайды оставляем целиком)
    var INS_SH_OP = 49, INS_SH_DIR = 135, INS_SH_DIST = 15, INS_SH_SOFT = 70; // тень вставок: чёрная, opacity в %% UI
    // Тайминги анимаций вставок (вход/выход, guard, noexit, пик наезда, вылет из-за
    // спины) считает Python и кладёт готовые ключи в ins.anim — в шаблоне их больше
    // не досчитываем: превью читает те же ключи из плана сцены (задание C)
    var TR_IN = 0.386, TR_SFX_LEAD = 0.083;    // Quick2 до стыка / whoosh ещё раньше
    // ====================================
    var W=%(w)d, H=%(h)d, FPS=%(fps)s, DUR=%(dur).4f;
    var CAM=%(cams)s;        // [{path, clips:[[start,end,in,out,enabled,scale],...]}, ...]
    var SUBS=%(subs)s;       // [[start,end,"WORD",hl,row,gend], ...] hl=1 жёлтое, row=ряд стопки, gend=общий конец
    var POP=%(pop)s;         // SFX «поп» для жёлтых слов или ""
    var CENSOR=%(censor)s;   // [[start,end], ...] сек — окна мьюта голоса (плохие слова)
    var EXPOSURE=%(exposure)g;  // яркость: Lumetri Exposure на все клипы камер (0 = не вешать)
    var ROTO=%(roto)s;          // [{ci,ts,te,cs,scale,mf,mask}, ...] — сплошное рото персонажа по видимой камере (весь хрон); mf = маска мельче исходника в mf раз
    var INTRO_GROUPS=%(intro_groups)s;  // [[{words,color,times},...], ...] — интро по прекомпам (кросс-фейд между группами)
    var INTRO_FONT=%(intro_font)s, INTRO_HL_FONT=%(intro_hl_font)s%(intro_fill_decl)s;  // шрифты интро (обычный/выделение); по умолч. = как субтитры
    var INTRO_MODE=%(intro_mode)s;  // "word" пословно (слой на слово) | "line" построчно (слой на строку, раскладка AE)
    var INTRO_GLOW=%(intro_glow)g;  // Glow Intensity на интро-тексте (AE-дефолт 1.0)
    var INTRO_SCALE=%(intro_scale)g, INTRO_Y=%(intro_y)g;  // общий масштаб (%%) и сдвиг по вертикали (px) ВСЕГО
                                    // интро: висят на нуле «интро», то есть двигают/масштабируют все прекомпы разом.
                                    // Опускание под INTRO_SAFE_TOP считает Python (задание Q2) — здесь только поправка Scale.
    var INTRO_ON2=%(intro_on2)s;    // [0|1 на группу] — группа появляется на перебивке (Камера 2): свой нул
%(intro_front_decl)s%(intro_ly_decl)s%(intro_above_roto_decl)s    var INTRO_IDY=%(intro_idy)s;    // [px на группу] — опускание блока под INTRO_SAFE_TOP, считает Python (задание Q2)
    var INTRO_Y2=%(intro_y2)g;      // сдвиг по вертикали (px) нула «интро на кам2» ПОВЕРХ INTRO_Y:
                                    // на перебивке кадр другой, и текст за спиной просится ниже
    var INTRO_WIDE=3;               // ширина интро-прекомпа в долях кадра: прекомп шире кадра, чтобы
                                    // размер текста поджимался СКАЛОЙ СЛОЯ в мастере, не заходя в композ.
                                    // Текст внутри всегда раскладывается в полный кегль — ужимание
                                    // длинных строк (автофит) считает Python в плане (задание BP)
    var INSERTS=%(inserts)s; // [{t:"photo"|"video",style:"cam2"|"cam1",media,start,end,scale,sc,mw,mh,x,y,front,oncam2}, ...] x/y = сдвиг точки покоя, px; sc = ручной масштаб в %% от авто (mw/mh — форма маски); front=видео перед человеком; oncam2=стиль кам1, но в кадре перебивка
    var SUB_HIDE=%(sub_hide)s;  // [[t, opacity], ...] — уход субтитров на rise-вставках
    var TRANS=%(trans)s, TRANS_SFX=%(trans_sfx)s;  // Quick 2.mov + whoosh для видеовставок
    var CAM1_SCALE=%(cam1scale)s;  // [[frame, percent], ...] зум Null камеры 1 — правь/очисти под видео
    var CAM1_HOLD=%(cam1hold)s;    // true = резкие скачки скейла (HOLD-кейфреймы), false = плавный наезд с откатом
    var CAM1_FIT=%(cam1_fit)g;     // масштаб кадра Камеры 1 в %% ЗАПОЛНЕНИЯ КОМПОЗИЦИИ при зуме нула 100%%:
                                   // 100 = кадр заполнен ровно, 120 = врезка на 20%%. Считается от РЕАЛЬНОГО
                                   // размера исходника (AE его знает), а не от масштаба из Премьера — тот
                                   // врёт, если файл пережали: 1080p-исходник приезжал со scale=50.4 и
                                   // вставал вполовину кадра. Рото-копия едет следом. Зум нула — поверх.
    var MUSIC=%(music)s, MUSIC_DB=%(music_db)g;  // музыка отдельным аудиослоем, уровень в dB
    var VOICE_DB=%(voice_db)g;                    // базовая громкость голоса (камера 1); цензура ныряет отсюда в −100
    var AUDIO_FADE=%(audio_fade)g;                // сек: микро-фейд громкости на краях каждого аудио-клипа (0 = выкл)
    var RISER=%(riser)s;                          // интро-SFX (ризер) или ""
    var DISCLAIMER=%(disclaimer)s, DISC_END=%(disc_end)g, DISC_SIZE=%(disc_size)s, DISC_Y=%(disc_y)d%(disc_lead_decl)s;

    app.beginUndoGroup("Reelsi build");
    // Лог сборки. Файл .aelog.txt заводит ХВОСТ (задание CD), а ошибки бывают раньше него —
    // копим и сливаем в файл после открытия. Пустых catch в шаблоне нет: молча терять
    // причину нельзя — так кривая наезда не применялась на всех ключах, и никто не узнал.
    var _log = null, _pending = [];
    function _LOG(msg){
        if (_log){ try{ _log.writeln(msg); }catch(e){} }
        else { _pending.push(msg); $.writeln("[reelsi] " + msg); }  // ручной режим: видно в консоли
    }
    // setTemporalEaseAtKey ждёт РОВНО столько KeyframeEase, сколько измерений у свойства,
    // а не value.length: Position 1-мерна, и на 2D-нуле value.length=2 роняет вызов
    // («Value array does not have 1 elements»). Пробуем value.length, при отказе — один
    // элемент (задание CE): раньше откат жил в двух местах (easePair/bez), а в зуме камеры 1
    // его не было, и кривая наезда МОЛЧА не применялась ни на одном ключе. Сводим все три
    // места сюда; не вышло и с единицей — в лог, а не в пустоту.
    function temporalEase(prop, infIn, infOut){
        function v(a, k){ return a instanceof Array ? (a[k-1] || a[a.length-1]) : a; }  // скаляр на все ключи или массив на каждый
        function arr(inf, d){ var a=[]; for(var q=0;q<d;q++) a.push(new KeyframeEase(0, inf)); return a; }
        function apply(d){ for(var k=1;k<=prop.numKeys;k++)
            prop.setTemporalEaseAtKey(k, arr(v(infIn,k),d), arr(v(infOut,k),d)); }
        try{ apply(prop.isSpatial ? 1 : (prop.value.length||1)); }  // Position пространственна: AE ждёт ровно 1, попытка с 2 печатала ошибку в лог на каждом ключе
        catch(e){ try{ apply(1); }catch(e2){ _LOG("temporalEase на «"+prop.name+"»: "+e2); } }
    }
    // импорт с дедупликацией: если файл уже есть в проекте — переиспользуем (важно для «один jsx на всё»)
    function imp(p){ var f=new File(p); if(!f.exists){%(imp_miss)s return null;}
        for(var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
            if((it instanceof FootageItem) && it.mainSource && it.mainSource.file
               && it.mainSource.file.fsName==f.fsName) return it; }
        return app.project.importFile(new ImportOptions(f)); }
    // КВАРТЕР-качество на ВСЕХ видеослоях сборки (камеры, видеовставки, рото-копии,
    // переходы): многослойные 4K-проекты в полном качестве жмут таймлайн и рендер,
    // а тянуть каждый клип в Draft руками после сборки — та же рутина на каждый ролик
    function draftQ(l){ try{ l.quality = LayerQuality.DRAFT; }catch(e){ try{ l.quality = 0.25; }catch(e2){} } }

    // бины в панели проекта: переиспользуем уже существующую папку с таким именем, иначе создаём
    var _bins={};%(binpfx_decl)s
    function bin(n){ if(_bins[n]===undefined){ var f=null;
        for(var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
            if((it instanceof FolderItem) && it.name==n){ f=it; break; } }
        if(!f){ try{ f=app.project.items.addFolder(n); }catch(e){ f=null; } } _bins[n]=f; } return _bins[n]; }
    function toBin(item,n){ if(item){ try{ var f=bin(%(bin_name)s); if(f && item.parentFolder!==f) item.parentFolder=f; }catch(e){} } }

    // 2 кейфрейма -> кривая cubic-bezier(0.35,0.01,0.10,0.99): key1 out-influence 35, key2 in-influence 90
    function easePair(prop){
        for (var k=1;k<=prop.numKeys;k++)
            prop.setInterpolationTypeAtKey(k, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
        if (prop.numKeys<2) return;
        temporalEase(prop, HL_EASE_IN, HL_EASE_OUT);   // Position 1-мерна — откат внутри (задание CE)
    }

    var main = app.project.items.addComp(%(name)s, W, H, 1.0, Math.max(DUR,1)%(comp_dur)s, FPS);

    // ---- intro: riser SFX + disclaimer text (fades out) ----
    if (RISER){ var rf=imp(RISER); if(rf){ toBin(rf,"Интро"); var rl=main.layers.add(rf); rl.name="Интро SFX"; %(riser_place)s%(riser_tail)s } }
    if (DISCLAIMER){
        var dl=main.layers.addText(DISCLAIMER);
        var dsp=dl.property("ADBE Text Properties").property("ADBE Text Document");
        var dd=dsp.value; dd.resetCharStyle(); dd.resetParagraphStyle(); dd.text=DISCLAIMER;
        try{dd.font=FONT;}catch(e){} dd.fontSize=DISC_SIZE; dd.fillColor=[1,1,1]; dd.applyFill=true;
        try{dd.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}
        %(disc_lead_js)sdsp.setValue(dd);
        dl.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, DISC_Y]);
        var dop=dl.property("ADBE Transform Group").property("ADBE Opacity");
        dop.setValueAtTime(Math.max(0,DISC_END-0.35), 100); dop.setValueAtTime(DISC_END, 0);
        dl.outPoint=DISC_END;
        try{ var g=dl.property("ADBE Effect Parade").addProperty("ADBE Glo2");
             try{g.property("Glow Radius").setValue(42);}catch(e){} }catch(e){}
    }

    // ---- subtitle precomp: слой на слово (база 140 белая; жёлтые = цвет+slide-up+opacity+стопка) ----
    // Ширина — SUB_WIDE× кадра (обрезка при sub_scale < 100), высота — как у кадра
    var SUB_WIDE=3;
    var SW=Math.round(W*SUB_WIDE);
    var subc = app.project.items.addComp(%(sub_comp_name)s, SW, H, 1.0, Math.max(DUR,1), FPS);
    toBin(subc,"Субтитры");                     // в корне — только основные композиции
    var FITW = W*0.92;                          // длинные слова ужимаем под эту ширину
%(sub_loop)s
    // ---- поп-SFX на каждое жёлтое слово (в момент появления) ----
    if (POP){ var popItem=imp(POP);
        if (popItem){ for (var pi=0; pi<SUBS.length; pi++){ if (SUBS[pi][3]){
            var pl=main.layers.add(popItem); pl.name="Поп"; %(pop_place)s
            %(pop_tail)s } } } }  // тише%(glitch_sfx)s

    // ---- cameras: footage layers parented to a Null named after the camera ----
    var cam1Layers = [];   // {lay,a,b} клипов Камеры 1 — чтобы ставить рото прямо над своим клипом
    function addCam(track, isSecond, label){
        // нул создаётся и ИМЕНУЕТСЯ ВСЕГДА (даже если путь камеры пуст) — иначе на 1-камерном
        // проекте без валидного path нул не появлялся вовсе и «вставки кам1»/интро оставались без родителя
        var nul = main.layers.addNull(Math.max(DUR,1)); nul.name = label; nul.enabled=false;
        if(!track || !track.path) return nul;
        var src = imp(track.path); if(!src) return nul; toBin(src,"Камеры");
        // масштаб «кадр заполнен ровно» — по РЕАЛЬНОМУ размеру исходника в AE. Для Камеры 1
        // считаем от него, а не от c[5] (масштаб из Премьера): тот описывает исходник, который
        // лежал в Премьере, и после пережатия 4K->1080p врёт вдвое (см. CAM1_FIT).
        var fitS = 100; try{ fitS = 100*Math.max(W/src.width, H/src.height); }catch(e){}
        var cl = track.clips;
        for (var j=0;j<cl.length;j++){
            var c = cl[j];
            if (isSecond && !c[4]) continue;     // скрытые клипы 2-й камеры не создаём
            var lay = main.layers.add(src);
            draftQ(lay);
            lay.startTime = (c[0]-c[2])/FPS;     // source frame `in` lands at timeline `start`
            lay.inPoint   = c[0]/FPS;
            lay.outPoint  = c[1]/FPS;
            lay.enabled   = c[4];
            if (isSecond){ try{ lay.audioEnabled = false; }catch(e){} }  // звук 2-й камеры выкл
            // Камера 1 — от заполнения кадра (CAM1_FIT), перебивки — как было в Премьере
            var csc = isSecond ? c[5] : fitS*CAM1_FIT/100;
            try{ lay.property("ADBE Transform Group").property("ADBE Scale").setValue([csc,csc]); }catch(e){}
            lay.parent = nul;
            if (EXPOSURE!=0){ try{ var lc=lay.property("ADBE Effect Parade").addProperty("ADBE Lumetri");  // яркость на все камеры
                try{ lc.property("ADBE Lumetri-0011").setValue(EXPOSURE); }catch(e){} }catch(e){} }
            if (!isSecond){ cam1Layers.push({lay:lay, a:c[0]/FPS, b:c[1]/FPS});  // для рото-порядка
                            try{ var alv0=lay.property("ADBE Audio Group").property("ADBE Audio Levels");
                                 alv0.setValue([VOICE_DB,VOICE_DB]);             // базовая громкость голоса
                                 // микро-фейд на краях клипа — убирает щелчки на жёстких склейках
                                 if (AUDIO_FADE>0 && (lay.outPoint-lay.inPoint) > 4*AUDIO_FADE){
                                     alv0.setValueAtTime(lay.inPoint, [-48,-48]);
                                     alv0.setValueAtTime(lay.inPoint+AUDIO_FADE, [VOICE_DB,VOICE_DB]);
                                     alv0.setValueAtTime(lay.outPoint-AUDIO_FADE, [VOICE_DB,VOICE_DB]);
                                     alv0.setValueAtTime(lay.outPoint, [-48,-48]); } }catch(e){}
                            if (CENSOR.length) censorLayer(lay); }               // цензура голоса базовой камеры (ныряет с VOICE_DB)
        }
        return nul;
    }
    // мьют одной буквы плохого слова: Audio Levels VOICE_DB -> -100 -> -100 -> VOICE_DB
    function censorLayer(lay){
        var alv; try{ alv = lay.property("ADBE Audio Group").property("ADBE Audio Levels"); }catch(e){ return; }
        if(!alv) return;
        for (var q=0; q<CENSOR.length; q++){
            var cs=CENSOR[q][0], ce=CENSOR[q][1];
            if (ce<=lay.inPoint || cs>=lay.outPoint) continue;      // окно не в этом клипе
            var a=Math.max(cs, lay.inPoint), b=Math.min(ce, lay.outPoint);
            alv.setValueAtTime(Math.max(lay.inPoint, a-0.02), [VOICE_DB,VOICE_DB]);
            alv.setValueAtTime(a, [-100,-100]);
            alv.setValueAtTime(b, [-100,-100]);
            alv.setValueAtTime(Math.min(lay.outPoint, b+0.02), [VOICE_DB,VOICE_DB]);
        }
    }
    // a Null per camera, named "Камера N" (supports 1..4)
    var nulls = [];
    for (var ci=0; ci<CAM.length; ci++)
        nulls[ci] = addCam(CAM[ci], ci>0, "Камера "+(ci+1));
    var cam1null = nulls[0];%(cam1_anchor)s%(intro_shade_js)s
    // камера «первого кадра» в момент t: верхний включённый клип, покрывающий t (верхняя дорожка побеждает)
    function camAt(t){ var f=t*FPS+1e-4, best=0;
        for (var c=0;c<CAM.length;c++){ var cls=CAM[c].clips||[];
            for (var q=0;q<cls.length;q++){ var cl=cls[q]; if(cl[4] && f>=cl[0] && f<cl[1]) best=c; } }
        return best; }

    // нулы общего управления вставками (рядом с нулами камер):
    //  «вставки кам1» — привязан к Null Камеры 1 (следует за её зумом), тождественный (position 0,0);
    //  «вставки кам2» — свободный (не привязан к камере), в мировом начале координат.
    var insNull1 = main.layers.addNull(Math.max(DUR,1)); insNull1.name="вставки кам1"; insNull1.enabled=false;
    if(cam1null){ insNull1.parent=cam1null;
        insNull1.property("ADBE Transform Group").property("ADBE Position").setValue([0,0]); }
    var insNull2 = main.layers.addNull(Math.max(DUR,1)); insNull2.name="вставки кам2"; insNull2.enabled=false;
    insNull2.property("ADBE Transform Group").property("ADBE Position").setValue([0,0]);
    //  «вставки кам1 на кам2» — тот же вылет из-за спины, но для вставок, попавших на перебивку.
    //  СВОБОДНЫЙ нул (в центре кадра, координаты те же локальные): привязка к Камере 1 тащила бы
    //  за собой её зум-дрейф 100-160%%, хотя самой Камеры 1 в кадре в этот момент нет — фото
    //  необъяснимо ездило и меняло размер. Двигая этот нул, правишь сразу все такие вставки.
    var insNull1b = main.layers.addNull(Math.max(DUR,1)); insNull1b.name="вставки кам1 на кам2"; insNull1b.enabled=false;
    insNull1b.property("ADBE Transform Group").property("ADBE Position").setValue([W/2,H/2]);
    // «интро» — ОДИН общий нул для всех интро-прекомпов, привязан к Null Камеры 1 (следует за её зумом)
    var introNull = main.layers.addNull(Math.max(DUR,1)); introNull.name="интро"; introNull.enabled=false;
    if(cam1null){ introNull.parent=cam1null;
        introNull.property("ADBE Transform Group").property("ADBE Position").setValue([%(intro_x_js)s,INTRO_Y]); }
    else introNull.property("ADBE Transform Group").property("ADBE Position").setValue([W/2%(intro_x_p)s,H/2+INTRO_Y]);
    // общий масштаб интро — на нуле, а не на прекомпах: внутри них раскладка слов уже посчитана
    // в пикселях, а автофит длинных строк (INTRO_FIT_W) должен остаться своим у каждого прекомпа
    if (INTRO_SCALE!=100)
        try{ introNull.property("ADBE Transform Group").property("ADBE Scale").setValue([INTRO_SCALE,INTRO_SCALE]); }catch(e){}
    // «интро на кам2» — ВТОРОЙ такой же нул для групп, выпавших на перебивку (INTRO_ON2).
    // Устроен один в один как «интро» (родитель — Null Камеры 1, тот же масштаб), отличается
    // только своим сдвигом INTRO_Y2: на кам2 кадр другой и текст за спиной ставят ниже.
    // Двигая этот нул, правишь разом все интро-прекомпы, попавшие на перебивку.
    var introNull2 = main.layers.addNull(Math.max(DUR,1)); introNull2.name="интро на кам2"; introNull2.enabled=false;
    if(cam1null){ introNull2.parent=cam1null;
        introNull2.property("ADBE Transform Group").property("ADBE Position").setValue([%(intro_x_js)s,INTRO_Y+INTRO_Y2]); }
    else introNull2.property("ADBE Transform Group").property("ADBE Position").setValue([W/2%(intro_x_p)s,H/2+INTRO_Y+INTRO_Y2]);
    if (INTRO_SCALE!=100)
        try{ introNull2.property("ADBE Transform Group").property("ADBE Scale").setValue([INTRO_SCALE,INTRO_SCALE]); }catch(e){}

    // optional zoom animation on Camera-1 Null
    if (cam1null && CAM1_SCALE.length){
        var sc = cam1null.property("ADBE Transform Group").property("ADBE Scale");
        var CAM1_EASE=%(cam1_ease)s;  // [[in,out], ...] влияние ease на КАЖДЫЙ ключ — посчитано в Python
        for (var z=0; z<CAM1_SCALE.length; z++)
            sc.setValueAtTime(CAM1_SCALE[z][0]/FPS, [CAM1_SCALE[z][1], CAM1_SCALE[z][1]]);
        if (CAM1_HOLD){                           // джамп-кат: скейл прыгает мгновенно, без анимации
            for (var kh=1; kh<=sc.numKeys; kh++)
                sc.setInterpolationTypeAtKey(kh, KeyframeInterpolationType.HOLD, KeyframeInterpolationType.HOLD);
        } else {                                  // pulse/дрейф: BEZIER + фирменная кривая из данных
            for (var k=1; k<=sc.numKeys; k++)
                sc.setInterpolationTypeAtKey(k, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
            // cubic-bezier(0.35,0.01,0.10,0.99) только на участках большой->малый:
            // out 35 / in 90 выставил Python по соседям (pulse) или режиму ключа (drift)
            var eIns=[], eOuts=[];
            for (var z2=0; z2<sc.numKeys; z2++){
                var ee=CAM1_EASE[z2]||[%(ease_default)g,%(ease_default)g];
                eIns.push(ee[0]); eOuts.push(ee[1]);
            }
            // Scale 2D-нула: value.length=2, а AE ждёт 1 — откат внутри, ошибка не прячется (задание CE)
            temporalEase(sc, eIns, eOuts);
        }
    }

    // ---- music as an audio layer (at MUSIC_DB) ----
    if (MUSIC){
        var ma = imp(MUSIC); toBin(ma,"Аудио");
        if (ma){
            var ml = main.layers.add(ma); ml.name = "Музыка"; ml.startTime = 0;
            try{ ml.property("ADBE Audio Group").property("ADBE Audio Levels").setValue([MUSIC_DB, MUSIC_DB]); }catch(e){}
        }
    }

    // ---- вставки фото/видео (ниже субтитров, выше камер) ----
    function addFX(L, mn){ try{ return L.property("ADBE Effect Parade").addProperty(mn); }catch(e){ return null; } }
    function setP(fx, mn, v){ if(fx){ try{ fx.property(mn).setValue(v); }catch(e){} } }
    function bez(prop){        // Bezier + cubic-bezier(0.35,0.01,0.10,0.99): out-влияние 35, in-влияние 90
        for(var k=1;k<=prop.numKeys;k++)
            prop.setInterpolationTypeAtKey(k, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
        temporalEase(prop, HL_EASE_IN, HL_EASE_OUT); }
    function applyKeyframes(prop, keys, dim2){   // ключи из ins.anim (посчитаны в Python)
        if (!keys || !keys.length) return;
        for (var ka=0; ka<keys.length; ka++){
            var kv=keys[ka][1];
            prop.setValueAtTime(keys[ka][0], dim2?[kv,kv]:kv);
        }
        bez(prop); }
    var transItem  = TRANS ? imp(TRANS) : null;      toBin(transItem,"Переходы");
    var whooshItem = TRANS_SFX ? imp(TRANS_SFX) : null; toBin(whooshItem,"Переходы");
    var transLayers=[];    // слои переходов — поднимаем над рото и фронт-видео после их сборки (вспышка горит поверх всего)
    function addTransAt(cut){
        if(transItem){  var tl=main.layers.add(transItem);  tl.name="Переход"; %(trans_place)s%(trans_tail)s
            draftQ(tl);
            try{ tl.blendingMode=BlendingMode.ADD; }catch(e){}     // Quick2 наложением Add
            transLayers.push(tl); }
        if(whooshItem){ var wl=main.layers.add(whooshItem); wl.name="Whoosh"; %(wsfx_place)s
            %(wsfx_tail)s } }  // whoosh тише
    function dropShadow(L, op){                    // белая тень интро-текста (не зависит от стиля вставок INS_FX)
        var ds=addFX(L,"ADBE Drop Shadow");
        setP(ds,"ADBE Drop Shadow-0001",[1,1,1]); setP(ds,"ADBE Drop Shadow-0002",op);
        setP(ds,"ADBE Drop Shadow-0003",135); setP(ds,"ADBE Drop Shadow-0004",0); setP(ds,"ADBE Drop Shadow-0005",287); }%(intro_comp_shadow_fn)s
    function insFX(L, kind){                       // эффекты фото-вставки; kind: "cam1"|"cam2"
        var ds=addFX(L,"ADBE Drop Shadow");
        if (INS_FX=="white"){                      // старый вид (комп 1221): белая тень + Simple Choker
            setP(ds,"ADBE Drop Shadow-0001",[1,1,1]); setP(ds,"ADBE Drop Shadow-0002", kind=="cam1"?7:255);
            setP(ds,"ADBE Drop Shadow-0003",135); setP(ds,"ADBE Drop Shadow-0004",0); setP(ds,"ADBE Drop Shadow-0005",287);
            setP(addFX(L,"ADBE Simple Choker"),"ADBE Simple Choker-0002", kind=="cam1"?-61.6:-87.2);
        } else {                                   // "card": чёрная тень (UI-проценты -> 0..255); маска «Скругление» вешается на слой отдельно
            setP(ds,"ADBE Drop Shadow-0001",[0,0,0]); setP(ds,"ADBE Drop Shadow-0002",INS_SH_OP/100*255);
            setP(ds,"ADBE Drop Shadow-0003",INS_SH_DIR); setP(ds,"ADBE Drop Shadow-0004",INS_SH_DIST); setP(ds,"ADBE Drop Shadow-0005",INS_SH_SOFT);
        } }
    function roundMask(L, x0, y0, x1, y1, r){      // маска-прямоугольник со скруглёнными углами (в координатах слоя)
        try{
            r=Math.min(r,(x1-x0)/2,(y1-y0)/2); var k=r*0.5523;
            var sh=new Shape(); sh.closed=true;
            sh.vertices   =[[x0+r,y0],[x1-r,y0],[x1,y0+r],[x1,y1-r],[x1-r,y1],[x0+r,y1],[x0,y1-r],[x0,y0+r]];
            sh.inTangents =[[-k,0],[0,0],[0,-k],[0,0],[k,0],[0,0],[0,k],[0,0]];
            sh.outTangents=[[0,0],[k,0],[0,0],[0,k],[0,0],[-k,0],[0,0],[0,-k]];
            var m=L.property("ADBE Mask Parade").addProperty("ADBE Mask Atom");
            m.name="Скругление"; m.property("ADBE Mask Shape").setValue(sh);
        }catch(e){} }
    function addMosaic(L, on){                     // мозаика 64x64, sharp colors, дрожание блоков wiggle(1,15)
        var mo=addFX(L,"ADBE Mosaic");
        setP(mo,"ADBE Mosaic-0001",64); setP(mo,"ADBE Mosaic-0002",64); setP(mo,"ADBE Mosaic-0003",1);
        if(mo){ try{ mo.property("ADBE Mosaic-0001").expression="wiggle(1,15)"; }catch(e){}
                try{ mo.property("ADBE Mosaic-0002").expression="wiggle(1,15)"; }catch(e){}
                try{ mo.enabled = !!on; }catch(e){} } }   // эффект есть всегда, включён только если mosaic

    // Перекрывающиеся вставки (cam1 и cam2) НЕ разъезжаются: каждая следующая ложится поверх
    // предыдущей (позже добавленный слой выше в стеке), как и в предпросмотре. Разъезд по X
    // убрали 2026-08-08 — пользователь правил тайминги вставок под «одна за другой», а сосед,
    // ещё не ушедший с экрана, вдруг отъезжал вбок посреди своего вылета.

    var photoLayers = [];
    var videoLayers = [];
    for (var ii=0; ii<INSERTS.length; ii++){
        var ins=INSERTS[ii], t0=ins.start, t1=ins.end;
        var fname=(""+ins.media).replace(/^.*[\\\/]/,'');
        var ix=ins.x||0, iy=ins.y||0;                   // ручной сдвиг ТОЧКИ ПОКОЯ (вход/выход считаются от неё)
        var isc=(ins.sc==null?100:ins.sc)/100;          // ручной масштаб, доля от авто (фото — от карточки, видео — от заполнения кадра)
        if (ins.t=="video"){
            var vit=imp(ins.media); if(!vit) continue; toBin(vit,"Вставки");
            var vl=main.layers.add(vit); vl.name="Вставка: "+fname;
            draftQ(vl);
            try{ vl.audioEnabled=false; }catch(e){}   // звук вставки глушим: дорожка идёт с камеры 1
            // sin = с какой секунды ФАЙЛА играть кусок (поле «файл с» в UI / in-point из Премьера).
            // Дальше конца файла не отступаем: кусок должен помещаться целиком, иначе AE
            // ругается на outPoint за пределами исходника и вставки в проекте не будет.
            var vdur=0; try{ vdur=vit.duration||0; }catch(e){}
            var vsin=Math.max(0, ins.sin||0);
            if(vdur>0) vsin=Math.min(vsin, Math.max(0, vdur-(t1-t0)));
            vl.startTime=t0-vsin;                      // source in-point попадает на t0 (как в Премьере)
            vl.inPoint=t0; vl.outPoint=t1;
            // масштаб заполнения и запас панорамы посчитал Python из размера файла
            // (fit/slackx/slacky). Размера нет -> полей нет: вставку не трогаем.
            if(ins.fit) try{ vl.property("ADBE Transform Group").property("ADBE Scale").setValue([ins.fit,ins.fit]); }catch(e){}
            // ландшафтное видео при fill вылезает по ширине в 1.5-3 раза — центр кадра почти
            // никогда не то, что надо показать; ix/iy = ручная панорама (в webui скраббером).
            // X/y уже ЗАЖАТЫ клампом в Python (план сцены): дальше запаса не пускаем —
            // там уже не кадр, а пустота (в предпросмотре так же).
            if(ins.x||ins.y) try{ vl.property("ADBE Transform Group").property("ADBE Position")
                .setValue([W/2+(ins.x||0), H/2+(ins.y||0)]); }catch(e){}
            if(ins.mosaic) addMosaic(vl, true);
            addTransAt(t0); if(!ins.noexit) addTransAt(t1);  // вход всегда; выход — если не обрезано по смене камеры
            videoLayers.push(vl);
            continue;
        }
        // фото -> прекомп (унификация размеров), анимация поверх прекомпа
        var pit=imp(ins.media); if(!pit) continue; toBin(pit,"Вставки");
        var pc=app.project.items.addComp("INS "+fname, W, H, 1.0, Math.max(DUR,1), FPS); toBin(pc,"Вставки");
        var inner=pc.layers.add(pit);
        inner.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, H/2]);
        try{ var _iw=pit.width; if(_iw){ var _f=W/_iw;   // тянем фото под ширину композа
            inner.property("ADBE Transform Group").property("ADBE Scale").setValue([_f*100,_f*100]); } }catch(e){}
        if(/\.gif$/i.test(fname)){ try{ inner.timeRemapEnabled=true;   // гифки зациклить
            inner.property("ADBE Time Remapping").expression="loopOut()"; inner.outPoint=Math.max(DUR,1); }catch(e){} }
        var L=main.layers.add(pc); L.inPoint=t0; L.outPoint=t1;
        photoLayers.push(L);
        %(ins_mask)s
        if (ins.style=="cam1"){                        // вылет из-за спины — привязан к нулу «вставки кам1», нужен ротоскоп
            // ВСЕГДА к insNull1 (нул «вставки кам1», привязан к Null Камеры 1) — все cam1-вставки едут за зумом кам1.
            // ИСКЛЮЧЕНИЕ — вставка попала на перебивку (стиль принудительно «Кам 1»): она висит на своём
            // нуле без зума Камеры 1, а точка покоя правится общей парой INS_C1_ON2_X/Y — сразу у всех таких.
            var onc2=!!ins.oncam2;
            if (onc2){ ix+=INS_C1_ON2_X; iy+=INS_C1_ON2_Y; }
            var par=onc2 ? insNull1b : (insNull1||((nulls&&nulls.length)?nulls[0]:null));
            var cx = par?0:W/2, cy = par?0:H/2;        // при родителе координаты локальные (0 = центр Null Камеры 1)
            if (par) L.parent=par;
            // не длиннее 30 кадров И не длиннее самой вставки: на коротком окне
            // (0.2-0.3с от ИИ) вылет из-за спины не успевал начаться — фото просто
            // не появлялось в кадре, хотя слой в таймлайне был
            var poP=L.property("ADBE Transform Group").property("ADBE Position");
            applyKeyframes(poP, ins.anim && ins.anim.position);   // ключи вылета посчитал Python (план сцены)
            var Ss=(ins.scale||44)*isc;                            // осевший scale: авторасчёт под карточку × ручной множитель sc
            try{ L.property("ADBE Transform Group").property("ADBE Scale").setValue([Ss,Ss]); }catch(e){}  // масштаб рото (поле Scale)
            addMosaic(L, ins.mosaic);                  // мозаика и на стиле кам1
            %(insfx_cam1)s
            L.name="Вставка: "+fname;                  // рото теперь сплошное — спец-метка не нужна
        } else {                                        // Камера 2: scale + blur + opacity
            L.name="Вставка: "+fname;
            if(insNull2) L.parent=insNull2;             // общий контроллер вставок кам2 (нул в мировом начале)
            // две cam2-вставки в одном окне раньше разводили по X (верхняя прятала нижнюю) —
            // теперь просто ложатся друг на друга, верхняя (позже добавленная) перекрывает нижнюю
            L.property("ADBE Transform Group").property("ADBE Position").setValue([%(ins_c2x_pos)s+ix, INS_C2_Y+iy]);
            applyKeyframes(L.property("ADBE Transform Group").property("ADBE Position"),
                           ins.anim && ins.anim.position);
            addMosaic(L, ins.mosaic);
            %(insfx_cam2)s
            if(ins.anim && ins.anim.blur){
                var bl=addFX(L,"ADBE Box Blur2");
                if(bl){ setP(bl,"ADBE Box Blur2-0004",0);    // Repeat Edge Pixels всегда выкл
                    applyKeyframes(bl.property("ADBE Box Blur2-0001"), ins.anim.blur); }
            }
            // наезд считаем ОТ осевшего масштаба (PEAK/BASE ≈ 2.27×), а не константой 100:
            // у крупной карточки (sc>227%%) фиксированный пик оказывался МЕНЬШЕ конечного
            // размера — вместо наезда вставка раздувалась внутрь кадра. Ключи наезда,
            // opacity и блюра посчитал Python — план сцены (задание C)
            applyKeyframes(L.property("ADBE Transform Group").property("ADBE Scale"),
                           ins.anim && ins.anim.scale, true);
            applyKeyframes(L.property("ADBE Transform Group").property("ADBE Opacity"),
                           ins.anim && ins.anim.opacity);
        }
        %(ins_wiggle)s
    }

    // ---- интро-текст: по прекомпу на группу строк; между группами кросс-фейд по opacity ----
    var introLayers = [];%(intro_front_arr_decl)s%(intro_above_roto_arr_decl)s%(intro_fx_decl)s
    if (INTRO_GROUPS.length){
        var LINE_STEP=160, F_DUR=0.3, HOLD=1.0, F_OUT=0.75, F_FADE=%(intro_fade)g;
        function introDoc(tl, txt, col%(accent_params)s%(fill_params)s){
            var sp=tl.property("ADBE Text Properties").property("ADBE Text Document");
            var dd=sp.value; dd.resetCharStyle(); dd.resetParagraphStyle(); dd.text=""+txt;
            try{dd.font=%(accent_font_pick)s;}catch(e){ try{dd.font=INTRO_FONT;}catch(e2){} }
            try{dd.fauxBold=(col=="yellow"&&HL_BOLD);}catch(e){} dd.fontSize=FONT_SIZE;
            dd.fillColor=%(intro_fill_pick)s; dd.applyFill=true;
            try{dd.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}
            sp.setValue(dd);
        }
        function introW(tl){ try{ return tl.sourceRectAtTime(0,false).width; }catch(e){ return 0; } }%(intro_back_scale_fn)s%(intro_word_shadow_fn)s%(intro_anim_fx_fn)s%(intro_hl_glow_fn)s
        for (var gI=0; gI<INTRO_GROUPS.length; gI++){
            var GRP=INTRO_GROUPS[gI]; if(!GRP.length) continue;%(intro_group_flags)s
            var gMax=0, gMin=1e9;
            for (var mi2=0; mi2<GRP.length; mi2++){ var tt=GRP[mi2].times||[];
                for (var mj=0; mj<tt.length; mj++){ if(tt[mj]>gMax) gMax=tt[mj]; if(tt[mj]<gMin) gMin=tt[mj]; } }
            if(gMin>=1e9) gMin=0;
            var introDur=gMax + F_DUR + HOLD + F_OUT;
            // прекомпы интро — в свой бин, как камеры/вставки/рото: в корне панели
            // проекта остаются только основные композиции (2026-08-04).
            // Ширина — INTRO_WIDE× кадра (текст в полный кегль, размер правится
            // скейлом слоя в мастере), высота — как у кадра.
            var IW=Math.round(W*INTRO_WIDE);
            var ic=app.project.items.addComp("текст интро"+(INTRO_GROUPS.length>1?(" "+(gI+1)):""), IW, H, 1.0, Math.max(introDur,1), FPS);
            toBin(ic,"Интро");
            %(intro_line_layout)s
                if(!wds.length) continue;
                if (INTRO_MODE=="line"){                        // одна строка = один слой (раскладка AE), фейд по 1-му слову
                    var Ll=ic.layers.addText(""); introDoc(Ll, wds.join(" "), ln.color%(accent_call)s%(fill_call)s);%(intro_back_scale_line)s%(intro_word_shadow_line)s
                    %(intro_back_scale_line_w)s
                    Ll.property("ADBE Transform Group").property("ADBE Position").setValue([IW/2, lineY]);
                    var t0l=1e9; for(var z=0;z<tms.length;z++) if(tms[z]<t0l) t0l=tms[z]; if(t0l>=1e9)t0l=0; if(t0l<0)t0l=0;
                    %(intro_line_anim)s
                    continue;
                }
                // пословно: ширина всей строки и каждого слова -> раскладка как единый абзац (интервалы точные)
                var tmp=ic.layers.addText(""); introDoc(tmp, wds.join(" "), ln.color%(accent_call)s%(fill_call)s); var lineW=introW(tmp);%(intro_back_scale_tmp)s tmp.remove();
                if(lineW>maxLineW) maxLineW=lineW;
                var wl=[], ww=[], sumW=0;
                for (var wj=0; wj<wds.length; wj++){
                    var L2=ic.layers.addText(""); introDoc(L2, wds[wj], ln.color%(accent_call)s%(fill_call)s);%(intro_back_scale_word)s%(intro_word_shadow_word)s
                    var wpx=introW(L2);%(intro_back_scale_wpx)s wl.push(L2); ww.push(wpx); sumW+=wpx;
                }
                var SPACE=(wds.length>1)?((lineW-sumW)/(wds.length-1)):0;
                var x=IW/2 - lineW/2;
                for (var wj2=0; wj2<wds.length; wj2++){
                    wl[wj2].property("ADBE Transform Group").property("ADBE Position").setValue([x+ww[wj2]/2, lineY]);
                    %(intro_word_anim)s
                    x += ww[wj2] + SPACE;
                }
            }
            var iL=main.layers.add(ic); iL.name=ic.name;
            %(intro_front_route)s%(intro_above_roto_route)s
            var last=(gI==INTRO_GROUPS.length-1);
            var inAt=(gI==0&&gMin<3)?0:gMin;                // 1-я группа видна с 0 ТОЛЬКО если она реально в начале; серединные — по 1-му своему слову
            // outStart не раньше конца фейд-ина: у группы из ОДНОГО слова gMax==inAt,
            // и ключ «100» на outStart затирал ключ «0» на inAt — акцент влетал
            // мгновенно вместо кросс-фейда (а при gMax чуть меньше inAt+F_DUR
            // выход начинался раньше входа).
            var outStart=last?(gMax+F_DUR+HOLD):Math.max(gMax, inAt+F_DUR);
            var outEnd=outStart+F_OUT;%(intro_fx_out)s
            // слой живёт с момента появления СВОИХ слов (серединный акцент не тянется с начала компа)
            iL.inPoint=(gI==0&&inAt==0)?0:inAt; iL.outPoint=outEnd;
            // родитель — общий нул «интро» (привязан к Null Камеры 1): все интро-прекомпы едут за кам1.
            // Группа, появляющаяся на перебивке, висит на своём нуле «интро на кам2» — чтобы её
            // (и все такие же) можно было опустить, не трогая интро на Камере 1
            var iPar = (INTRO_ON2[gI] ? introNull2 : introNull);
            // Масштаб слоя прекомпа = INTRO_SCALE (96.8%%) × ds. ds несёт и ручной масштаб
            // группы (задание O), и автофит длинных строк (задание BP) — оба считает
            // PYTHON в плане: здесь только применение, вторая копия формулы не заводится.
            // Применённый последним (перед позицией), он не даёт защитам считать
            // неотмасштабированный блок, а превью рисует ту же ds из плана.
            var gDs=(GRP[0].ds||100);
            var iSc=96.8*gDs/100;
            // опускание блока под INTRO_SAFE_TOP считает PYTHON (задание Q2): iDy живёт
            // в плане и шаблоне в одном месте, вторая копия формулы не заводится. От
            // неужатого масштаба (см. _intro_i_dy) — автофит режет только Scale.
            var iDy=INTRO_IDY[gI]||0;
            // Смещение ГРУППЫ (задание E): dx/dy приезжают в головной строке GRP[0] и
            // складываются ПОВЕРХ общего сдвига нула (INTRO_Y/INTRO_Y2 висят на нуле) —
            // общий сдвиг остаётся, группа двигается сама по себе. Нет dx/dy в данных
            // (дефолт 0/0) — gDx/gDy нулевые и позиция прежняя.
            var gDx=(GRP[0].dx||0), gDy=(GRP[0].dy||0);
            if(iPar){ iL.parent=iPar; iL.property("ADBE Transform Group").property("ADBE Position").setValue([gDx,-520.7894+iDy+gDy]); }
            else iL.property("ADBE Transform Group").property("ADBE Position").setValue([W/2+gDx, H/2-520.7894+iDy+gDy]);
            try{ iL.property("ADBE Transform Group").property("ADBE Scale").setValue([iSc,iSc]); }catch(e){}
            var iLop=iL.property("ADBE Transform Group").property("ADBE Opacity");
            if(gI==0&&inAt==0){ iLop.setValueAtTime(0,100); }
            else { iLop.setValueAtTime(inAt,0); iLop.setValueAtTime(inAt+F_DUR,100); easePair(iLop); }
            iLop.setValueAtTime(Math.max(outStart,outEnd-F_FADE),100); iLop.setValueAtTime(outEnd,0);
            %(intro_comp_glow)s
            %(intro_comp_shadow)s
        }
    }

    // ---- авто-ротоскоп НА ВЕСЬ ХРОН: сплошная копия персонажа по видимой камере ----
    // Слои снизу вверх: камеры -> вставки/интро-текст -> РОТО (человек всегда сверху) -> субтитры.
    // Каждый кусок = копия СВОЕЙ камеры + luma-матте, привязан к нулу СВОЕЙ камеры (пиксель-в-
    // пиксель с видимым кадром, включая зум). Все рото-слои shy — спрячь их кнопкой Shy в AE.
    var rotoLayers = [];
    if (ROTO.length && CAM.length && nulls.length){
        for (var ri=0; ri<ROTO.length; ri++){
            var rr=ROTO[ri]; var ci=(rr.ci||0);
            if (!CAM[ci] || !CAM[ci].path || !nulls[ci]) continue;
            var camSrc = imp(CAM[ci].path);            // исходник СВОЕЙ камеры (cam1 или перебивка cam2)
            var maskIt = imp(rr.mask); if(!maskIt || !camSrc) continue; toBin(maskIt,"Рото");
            var cc = main.layers.add(camSrc); cc.name="Рото камера";  // тот же участок источника
            draftQ(cc);
            cc.startTime=rr.cs; cc.inPoint=rr.ts; cc.outPoint=rr.te;
            try{ cc.audioEnabled=false; }catch(e){}
            cc.parent=nulls[ci];%(roto_pos_cc)s                    // сначала parent, ПОТОМ scale (иначе AE делит на зум Null)
            // ровно тот же масштаб, что у кадра своей камеры: рото-копия обязана лежать
            // пиксель-в-пиксель, иначе человек разъезжается с собственным кадром
            var rfit=100; try{ rfit = 100*Math.max(W/camSrc.width, H/camSrc.height); }catch(e){}
            var rsc = (ci==0) ? rfit*CAM1_FIT/100 : rr.scale;
            try{ cc.property("ADBE Transform Group").property("ADBE Scale").setValue([rsc,rsc]); }catch(e){}
            if (EXPOSURE!=0){ try{ var lc=cc.property("ADBE Effect Parade").addProperty("ADBE Lumetri");
                lc.property("ADBE Lumetri-0011").setValue(EXPOSURE); }catch(e){} }
            var mk = main.layers.add(maskIt); mk.name="Рото маска";   // альфа над копией
            mk.startTime=rr.ts; mk.inPoint=rr.ts; mk.outPoint=rr.te;
            mk.parent=nulls[ci];%(roto_pos_mk)s
            // маска может быть в уменьшенном разрешении (mf = во сколько раз мельче исходника)
            var msc=rsc*(rr.mf||1);
            try{ mk.property("ADBE Transform Group").property("ADBE Scale").setValue([msc,msc]); }catch(e){}
            try{ cc.shy=true; mk.shy=true; cc.label=9; mk.label=9; }catch(e){}   // рото-группа: shy + зелёная метка
            mk.moveBefore(cc);                          // маска прямо над копией
            try{ cc.setTrackMatte(mk, TrackMatteType.LUMA); }        // AE 23+
            catch(e){ try{ cc.trackMatteType=TrackMatteType.LUMA; }catch(e2){} }  // старый API
            rotoLayers.push(cc);
            rotoLayers.push(mk);
        }
        try{ main.hideShyLayers=true; }catch(e){}       // рото свёрнуто из таймлайна по умолчанию
    }

    // ---- subtitle precomp on top + Drop Shadow ----
    var subLayer = main.layers.add(subc);
    subLayer.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([SW/2, H/2]);
    subLayer.property("ADBE Transform Group").property("ADBE Position").setValue([W/2, H/2]);
    var subLayers = [subLayer];
%(sub_shadow_js)s    if (SUB_HIDE.length){
        applyKeyframes(subLayer.property("ADBE Transform Group").property("ADBE Opacity"), SUB_HIDE);
    }
%(sub_bg_js)s
    // ---- раскладка слоёв по порядку из стиля (задание FM) ----
    var LAYER_ORDER = %(layer_order)s;
    var layerGroups = {
        "subs": subLayers,
        "intro": introLayers,
        "photo": photoLayers,
        "video": videoLayers,
        "roto": rotoLayers
    };
    var transRaised = false;
    for (var loi = LAYER_ORDER.length - 1; loi >= 0; loi--){
        var grp = layerGroups[LAYER_ORDER[loi]];
        if (grp){
            for (var gi = 0; gi < grp.length; gi++){
                try{ grp[gi].moveToBeginning(); }catch(e){}
            }
        }
        if (LAYER_ORDER[loi] == "video"){
            for (var tv=0; tv<transLayers.length; tv++){ try{ transLayers[tv].moveToBeginning(); }catch(e){} }
            transRaised = true;
        }
    }
    if (!transRaised){
        for (var tv=0; tv<transLayers.length; tv++){ try{ transLayers[tv].moveToBeginning(); }catch(e){} }
    }
%(intro_above_roto_raise)s%(intro_front_raise)s
    // ---- ВСЕ нулы — одним блоком сразу под субтитрами ----
    // Список был поимённый, и каждый заведённый позже нул в него забывали дописать:
    // «вставки кам1 на кам2» и «интро на кам2» так и оставались закопаны между клипами
    // камер, а найти их в таймлайне можно было только прокруткой. Порядок задаём явно
    // (камеры → вставки → интро), а ХВОСТОМ добираем любой оставшийся нул композиции —
    // забыть новый нул больше нечем.
    var nullOrder=[];
    for (var mi=0; mi<CAM.length; mi++) if(nulls[mi]) nullOrder.push(nulls[mi]);
    nullOrder.push(insNull1, insNull2, insNull1b, introNull, introNull2);
    for (var qi2=1; qi2<=main.layers.length; qi2++){
        var qL=main.layer(qi2), isNull=false;
        try{ isNull=!!qL.nullLayer; }catch(e){ isNull=false; }   // у камер/светов свойства нет
        if(!isNull) continue;
        var known=false;
        for (var qj=0; qj<nullOrder.length; qj++) if(nullOrder[qj]===qL){ known=true; break; }
        if(!known) nullOrder.push(qL);
    }
    var nullAnchor=subLayer;
%(sub_bg_null_anchor)s%(sub_scale_js)s    for (var qn=0; qn<nullOrder.length; qn++){ var qN=nullOrder[qn]; if(!qN) continue;
        try{ qN.moveAfter(nullAnchor); nullAnchor=qN; }catch(e){} }

    try{ if (DISCLAIMER && dl) dl.moveToBeginning(); }catch(e){}   // дисклеймер поверх всего
%(top_line_js)s%(caption_js)s%(disc_end_js)s%(blur_js)s
%(dg_report)s%(tail)s})();
"""
