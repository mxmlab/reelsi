// SPDX-License-Identifier: AGPL-3.0-or-later
// Reelsi — AE project inspector.
// Запусти в After Effects: File > Scripts > Run Script File... на нужном проекте.
// Он выгрузит всю структуру проекта (композы, слои, трансформы, эффекты, кейфреймы,
// кривые безье, привязки, тексты) в JSON.
//
// Файл кладётся САМ, рядом с проектом: <проект>.inspect.json — диалога нет, чтобы
// путь был предсказуемым и дамп можно было прочитать без пересылки. Несохранённый
// проект (нет .aep на диске) — тогда спросим куда, как раньше.
// Дальше: python reelsi/verify_ae.py <проект>.inspect.json  — сверка с .jsx.
(function () {
    // ---- tiny JSON serializer (ExtendScript has no reliable JSON) ----
    function q(s){ s=String(s); var r="",i,c,o; for(i=0;i<s.length;i++){ c=s.charAt(i); o=s.charCodeAt(i);
        if(c=='"')r+='\\"'; else if(c=='\\')r+='\\\\'; else if(c=='\n')r+='\\n'; else if(c=='\r')r+='\\r';
        else if(c=='\t')r+='\\t'; else if(o<32)r+=' '; else r+=c; } return '"'+r+'"'; }
    function J(v){
        if(v===null||v===undefined) return "null";
        var t=typeof v;
        if(t=="number") return isFinite(v)? String(v):"null";
        if(t=="boolean") return v?"true":"false";
        if(t=="string") return q(v);
        if(v instanceof Array){ var a=[],i; for(i=0;i<v.length;i++) a.push(J(v[i])); return "["+a.join(",")+"]"; }
        var p=[],k; for(k in v){ if(v.hasOwnProperty(k)) p.push(q(k)+":"+J(v[k])); } return "{"+p.join(",")+"}";
    }
    function num(x){ return Math.round(x*10000)/10000; }
    function arr(v){ if(v instanceof Array){ var a=[],i; for(i=0;i<v.length;i++) a.push(num(v[i])); return a; } return num(v); }

    function val(p){
        try{
            var v=p.value;
            if(v && v.text!==undefined){ // TextDocument
                var d={text:v.text};
                try{d.font=v.font;}catch(e){} try{d.fontSize=v.fontSize;}catch(e){}
                try{d.fillColor=arr(v.fillColor);}catch(e){} try{d.justification=String(v.justification);}catch(e){}
                return d;
            }
            return arr(v);
        }catch(e){ return "?"; }
    }
    function ease(arrE){ var a=[],i; for(i=0;i<arrE.length;i++) a.push([num(arrE[i].speed),num(arrE[i].influence)]); return a; }

    function dumpProp(p){
        var o={name:p.name, mn:p.matchName};
        try{
            if(p.numKeys && p.numKeys>0){
                var keys=[],k;
                for(k=1;k<=p.numKeys;k++){
                    var kd={t:num(p.keyTime(k)), v:arr(p.keyValue(k))};
                    try{ kd.iI=String(p.keyInInterpolationType(k)); kd.oI=String(p.keyOutInterpolationType(k)); }catch(e){}
                    try{ kd.iE=ease(p.keyInTemporalEase(k)); kd.oE=ease(p.keyOutTemporalEase(k)); }catch(e){}
                    keys.push(kd);
                }
                o.keys=keys;
            } else {
                o.value=val(p);
            }
        }catch(e){ o.err=String(e); }
        return o;
    }
    function dumpGroup(g, depth){
        var out=[], i;
        if(!g || !g.numProperties) return out;
        for(i=1;i<=g.numProperties;i++){
            var p; try{ p=g.property(i); }catch(e){ continue; }
            if(!p) continue;
            if(p.propertyType==PropertyType.PROPERTY){
                out.push(dumpProp(p));
            } else if(depth>0){ // nested group (e.g. effect)
                out.push({name:p.name, mn:p.matchName, group:dumpGroup(p, depth-1)});
            } else {
                out.push({name:p.name, mn:p.matchName});
            }
        }
        return out;
    }

    function dumpLayer(L){
        var o={name:L.name, index:L.index};
        try{o.enabled=L.enabled;}catch(e){}
        try{o.inPoint=num(L.inPoint); o.outPoint=num(L.outPoint); o.startTime=num(L.startTime);}catch(e){}
        try{o.parent=L.parent?L.parent.name:null;}catch(e){}
        try{o.threeD=L.threeDLayer;}catch(e){}
        try{o.isNull=L.nullLayer;}catch(e){}   // нулы сверяются как ярус: они собираются в шапку компа
        try{o.source=L.source?L.source.name:null;}catch(e){}
        try{ if(L.property("ADBE Text Properties")) o.type="text"; }catch(e){}
        try{o.transform=dumpGroup(L.property("ADBE Transform Group"),1);}catch(e){}
        try{ var fx=L.property("ADBE Effect Parade"); if(fx && fx.numProperties>0) o.effects=dumpGroup(fx,2); }catch(e){}
        try{ var tp=L.property("ADBE Text Properties"); if(tp) o.text=dumpProp(tp.property("ADBE Text Document")); }catch(e){}
        return o;
    }

    var proj=app.project;
    var out={project:proj.file?proj.file.name:"untitled", comps:[]};
    var i, nlayers=0;
    for(i=1;i<=proj.numItems;i++){
        var it=proj.item(i);
        if(it instanceof CompItem){
            var c={name:it.name, w:it.width, h:it.height, fps:it.frameRate, dur:num(it.duration), layers:[]};
            var j;
            for(j=1;j<=it.numLayers;j++){ try{ c.layers.push(dumpLayer(it.layer(j))); nlayers++; }catch(e){} }
            out.comps.push(c);
        }
    }

    // Путь без диалога: рядом с .aep. Так дамп всегда лежит там, где его ждут.
    var f=null;
    if(proj.file){
        f=new File(proj.file.fsName.replace(/\.aep$/i,"")+".inspect.json");
    } else {
        f=File.saveDialog("Проект не сохранён — куда положить дамп (JSON)", "*.json");
        if(f && f.name.indexOf(".json")<0) f=new File(f.fsName+".json");
    }
    if(f){
        f.encoding="UTF-8"; f.open("w"); f.write(J(out)); f.close();
        alert("Готово: "+f.fsName+"\nКомпов: "+out.comps.length+", слоёв: "+nlayers);
    }
})();
