// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 Maxim Si
// LUT камер спикера: таблица .cube ложится на кадр превью НА ЛЕТУ.
//
// В проект After Effects LUT уезжает прожжённым в видео камеры: AE очень плохо
// работает с цветом. В превью прожигать нельзя — это запись файла на каждый кадр,
// — поэтому здесь тот же LUT живёт шейдером: кадр камеры рисуется в скрытый
// WebGL2-canvas, а оттуда уже на canvas превью (ipvCamPaint). Порядок как в сборке:
// сначала LUT, потом Lumetri из стиля — он висит фильтром 2D-контекста, то есть
// ложится ПОВЕРХ покрашенного кадра.
//
// Часть интерфейса Reelsi. Файлы static/app/ грузятся ПО ПОРЯДКУ ИМЁН обычными
// <script>-тегами: один общий скоуп.

const LUTMAX=1920;                 // больше по большей стороне не считаем: превью всё равно меньше
const LUTVS='#version 300 es\n'
  +'in vec2 aPos;out vec2 vTex;\n'
  +'void main(){vTex=aPos*0.5+0.5;gl_Position=vec4(aPos,0.0,1.0);}';
// Домен и полутексель — та же арифметика, что в lutSample: (c*(N-1)+0.5)/N — центр
// текселя при LINEAR-фильтрации. Без полутекселя цвет уезжал бы на полшага сетки.
const LUTFS='#version 300 es\n'
  +'precision highp float;\n'
  +'uniform highp sampler2D uVideo;uniform highp sampler3D uLut;\n'
  +'uniform vec3 uMin;uniform vec3 uMax;uniform float uN;\n'
  +'in vec2 vTex;out vec4 outColor;\n'
  +'void main(){\n'
  +' vec3 c=texture(uVideo,vTex).rgb;\n'
  +' vec3 k=clamp((c-uMin)/max(uMax-uMin,vec3(0.000001)),0.0,1.0);\n'
  +' outColor=vec4(texture(uLut,(k*(uN-1.0)+0.5)/uN).rgb,1.0);\n'
  +'}';

// ---- чистый расчёт по таблице (без WebGL) ----
// Цвет, который даёт шейдер: домен растягивается в 0..1, дальше — линейная
// интерполяция по узлам (LINEAR-фильтрация 3D-текстуры). Нужен стенду тестов: в
// node WebGL нет, а «на тождественном LUT цвет не меняется» надо проверить именно
// на боевой формуле — второй её копии в файле быть не должно.
// Порядок данных — как в файле .cube: красный меняется быстрее всех, поэтому он
// младший в индексе.
function lutSample(lut,r,g,b){
  const N=+((lut&&lut.size)||0),D=(lut&&lut.data)||[];
  const dmin=(lut&&lut.domain_min)||[0,0,0],dmax=(lut&&lut.domain_max)||[1,1,1];
  if(N<2||D.length<N*N*N*3)return [r,g,b];
  const ax=(v,i)=>{const lo=+dmin[i]||0,hi=(dmax[i]==null?1:+dmax[i]);
    let k=(hi>lo)?(v-lo)/(hi-lo):0;k=k<0?0:(k>1?1:k);return k*(N-1);};
  const x=ax(r,0),y=ax(g,1),z=ax(b,2);
  const i0=Math.floor(x),j0=Math.floor(y),k0=Math.floor(z);
  const i1=Math.min(i0+1,N-1),j1=Math.min(j0+1,N-1),k1=Math.min(k0+1,N-1);
  const fx=x-i0,fy=y-j0,fz=z-k0,out=[0,0,0];
  for(let ch=0;ch<3;ch++){
    const at=(i,j,k)=>D[((k*N+j)*N+i)*3+ch];
    const a=at(i0,j0,k0)+(at(i1,j0,k0)-at(i0,j0,k0))*fx;
    const b2=at(i0,j1,k0)+(at(i1,j1,k0)-at(i0,j1,k0))*fx;
    const c2=at(i0,j0,k1)+(at(i1,j0,k1)-at(i0,j0,k1))*fx;
    const d2=at(i0,j1,k1)+(at(i1,j1,k1)-at(i0,j1,k1))*fx;
    const e0=a+(b2-a)*fy,e1=c2+(d2-c2)*fy;
    out[ch]=e0+(e1-e0)*fz;}
  return out;}

// ---- таблицы: что уже приехало с сервера ----
const LUTTAB=new Map();      // путь -> {size,domain_min,domain_max,data,path}
const LUTWAIT=new Set();     // путь -> запрос уже идёт (иначе fetch на каждый кадр)
const LUTDEAD=new Set();     // путь -> не прочитался: пробовать на каждом кадре нельзя

// Клип, открытый в превью: IPV.xml — ровно тот XML, что загружен (шаги 2 и 3 рисует
// один плеер). Спикер берётся у СВОЕГО клипа, а не у выбранного в панели: превью
// могло остаться открытым от другого.
function lutClip(){
  if(typeof IPV==='undefined'||!IPV||!IPV.xml||typeof clipByXml!=='function')return null;
  return clipByXml(IPV.xml)||null;}

// Путь к .cube этой камеры из профиля спикера клипа ('' — LUT не задан).
function lutPath(ci){
  const c=lutClip(),spk=(c&&c.job&&c.job.speaker)||'';
  const tab=(spk&&typeof SPEAKERS!=='undefined'&&SPEAKERS[spk]&&SPEAKERS[spk].lut)||null;
  return String((tab&&tab[String(ci+1)])||'').trim();}

// Таблица LUT камеры: null — не задана, ещё грузится или не прочиталась.
function lutFor(ci){
  const p=lutPath(ci);
  if(!p||LUTDEAD.has(p))return null;
  const tab=LUTTAB.get(p);
  if(tab)return tab;
  if(LUTWAIT.has(p))return null;       // запрос уже идёт — второй на этот же кадр не нужен
  LUTWAIT.add(p);
  fetch('/api/lut?path='+encodeURIComponent(p)).then(r=>r.json()).then(d=>{
    LUTWAIT.delete(p);
    if(d&&d.ok&&d.size&&d.data){d.path=p;LUTTAB.set(p,d);return;}
    LUTDEAD.add(p);uiLog(t('LUT не прочитался: ')+errText(d));})
  .catch(e=>{LUTWAIT.delete(p);LUTDEAD.add(p);uiLog(t('LUT не прочитался: ')+e);});
  return null;}

// ---- WebGL2: покраска кадра ----
let LUT_GL=null;             // {ok,cv,gl,prog,...} — ОДИН контекст на всё превью
let LUT_NOLOG=false;         // «нет WebGL2» и прочие отказы пишем в лог один раз
let LUT_VFAIL=false;         // кадр не лёг в текстуру — тоже один раз

// Размер LUT-canvas: кадр целиком, но не больше LUTMAX по большей стороне.
// Считает ОДНА функция: по ней же ipvCamPaint пересчитывает координаты вырезки
// (lutKS), иначе зум и сдвиг разъехались бы с картинкой.
function lutSize(vw,vh){
  const k=(Math.max(vw,vh)>LUTMAX)?(LUTMAX/Math.max(vw,vh)):1;
  return [Math.max(1,Math.round(vw*k)),Math.max(1,Math.round(vh*k))];}

// Размеры кадра у ИСТОЧНИКА: у <video> это videoWidth/videoHeight, у картинки
// (кадры камер картинками, режим рендера без AE) — naturalWidth/naturalHeight.
// Одна дверь на lutSize/lutKS/lutApply: разойдись они — координаты вырезки поехали бы
// относительно картинки.
function lutWH(v){
  return [(v&&(v.videoWidth||v.naturalWidth))|0,(v&&(v.videoHeight||v.naturalHeight))|0];}

// Масштаб координат кадра в пиксели LUT-canvas: [1,1], если LUT не накладывается.
function lutKS(ci,v){
  const wh=lutWH(v);
  if(!wh[0]||!wh[1]||!lutFor(ci)||!lutGL())return [1,1];
  const s=lutSize(wh[0],wh[1]);
  return [s[0]/wh[0],s[1]/wh[1]];}

// float32 -> float16: значения LUT лежат в 0..1, но считаем общий случай (знак,
// экспонента, мантисса) — таблицу мог написать и генератор с отрицательными числами.
const LUTF32=new Float32Array(1),LUTI32=new Int32Array(LUTF32.buffer);
function lutHalf(d){
  const out=new Uint16Array(d.length);
  for(let i=0;i<d.length;i++){
    LUTF32[0]=d[i];const bits=LUTI32[0],sign=(bits>>>16)&0x8000;
    const e=((bits>>>23)&0xff)-127+15,m=(bits>>>13)&0x03ff;
    out[i]=(e<=0)?sign:((e>=31)?(sign|0x7c00):(sign|(e<<10)|m));}
  return out;}

// Запас 8 бит: округление, а не усечение — иначе вся таблица темнела бы на полшага.
function lutBytes(d){
  const out=new Uint8Array(d.length);
  for(let i=0;i<d.length;i++){const v=Math.round(d[i]*255);out[i]=v<0?0:(v>255?255:v);}
  return out;}

// Три попытки по точности: 32-битный float (линейная фильтрация — расширение),
// half-float (в WebGL2 фильтруется всегда) и 8 бит — последний запас, когда
// видеокарта не приняла ни то, ни другое. Возвращает 0, если не поднялось ничего.
function lutTexImage(gl,lut){
  const N=lut.size,d=lut.data;
  const tries=[
    {fmt:gl.RGB16F,type:gl.HALF_FLOAT,data:()=>lutHalf(d)},
    {fmt:gl.RGB8,type:gl.UNSIGNED_BYTE,data:()=>lutBytes(d)}];
  if(gl.getExtension('OES_texture_float_linear'))
    tries.unshift({fmt:gl.RGB32F,type:gl.FLOAT,data:()=>new Float32Array(d)});
  // Флип выставлен ради кадра видео (lutGL), но 3D-текстуру из массива WebGL2 при
  // UNPACK_FLIP_Y_WEBGL=true не принимает ВООБЩЕ: INVALID_OPERATION на каждый формат,
  // таблица оставалась пустой, и превью с LUT было чёрным. На время загрузки выключаем.
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,false);
  try{
    for(const cand of tries){
      while(gl.getError()!==gl.NO_ERROR){}   // чужая ошибка не должна решать за проверку формата
      try{
        gl.texImage3D(gl.TEXTURE_3D,0,cand.fmt,N,N,N,0,gl.RGB,cand.type,cand.data());
        if(gl.getError()===gl.NO_ERROR)return cand.fmt;}catch(e){}   // формат не поднялся
    }
    return 0;
  }finally{
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,true);   // кадр видео грузим перевёрнутым, как раньше
  }}

function lutMakeTex(gl,target,unit){
  gl.activeTexture(unit);
  const tex=gl.createTexture();
  gl.bindTexture(target,tex);
  gl.texParameteri(target,gl.TEXTURE_MIN_FILTER,gl.LINEAR);
  gl.texParameteri(target,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(target,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
  gl.texParameteri(target,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  if(target===gl.TEXTURE_3D)gl.texParameteri(target,gl.TEXTURE_WRAP_R,gl.CLAMP_TO_EDGE);
  return tex;}

function lutProgram(gl){
  const make=(kind,src)=>{const sh=gl.createShader(kind);gl.shaderSource(sh,src);
    gl.compileShader(sh);return gl.getShaderParameter(sh,gl.COMPILE_STATUS)?sh:null;};
  const vs=make(gl.VERTEX_SHADER,LUTVS),fs=make(gl.FRAGMENT_SHADER,LUTFS);
  if(!vs||!fs)return null;
  const prog=gl.createProgram();
  gl.attachShader(prog,vs);gl.attachShader(prog,fs);gl.linkProgram(prog);
  return gl.getProgramParameter(prog,gl.LINK_STATUS)?prog:null;}

// Контекст, программа и текстуры — один раз на страницу (не на кадр). Нет WebGL2 —
// превью работает без LUT: кадр идёт как есть, и об этом одна строка в логе.
function lutGL(){
  if(LUT_GL)return LUT_GL.ok?LUT_GL:null;
  let cv=null,gl=null;
  try{
    cv=document.createElement('canvas');   // вне DOM: композитор его не трогает, буфер жив до drawImage
    gl=cv.getContext('webgl2',{alpha:false,antialias:false,depth:false,stencil:false,
      premultipliedAlpha:false});
  }catch(e){gl=null;}
  LUT_GL={ok:false,cv:cv,gl:gl,prog:null,vtex:null,ltex:null,lkey:'',vw:0,vh:0};
  if(!gl){if(!LUT_NOLOG){LUT_NOLOG=true;uiLog(t('LUT: WebGL2 недоступен — превью без LUT'));}
    return null;}
  const prog=lutProgram(gl);
  if(!prog){if(!LUT_NOLOG){LUT_NOLOG=true;uiLog(t('LUT: шейдер не собрался — превью без LUT'));}
    return null;}
  gl.useProgram(prog);
  gl.uniform1i(gl.getUniformLocation(prog,'uVideo'),0);
  gl.uniform1i(gl.getUniformLocation(prog,'uLut'),1);
  // Полноэкранный треугольник: у квада по диагонали виден шов интерполяции
  const buf=gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER,buf);
  gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
  const aPos=gl.getAttribLocation(prog,'aPos');
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos,2,gl.FLOAT,false,0,0);
  // Кадр кладём «как видят глаза»: в GL начало координат снизу, у канвы — сверху,
  // без флипа картинка вышла бы перевёрнутой
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,true);
  LUT_GL.prog=prog;
  LUT_GL.vtex=lutMakeTex(gl,gl.TEXTURE_2D,gl.TEXTURE0);
  LUT_GL.ltex=lutMakeTex(gl,gl.TEXTURE_3D,gl.TEXTURE1);
  LUT_GL.uMin=gl.getUniformLocation(prog,'uMin');
  LUT_GL.uMax=gl.getUniformLocation(prog,'uMax');
  LUT_GL.uN=gl.getUniformLocation(prog,'uN');
  gl.activeTexture(gl.TEXTURE0);
  LUT_GL.ok=true;
  return LUT_GL;}

// Текстура LUT пересоздаётся только при смене таблицы: 33^3 точки — это не то, что
// стоит заливать в видеопамять на каждый кадр.
function lutTexLut(ctx,lut){
  if(ctx.lkey===lut.path)return;
  const gl=ctx.gl;
  gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_3D,ctx.ltex);
  if(!lutTexImage(gl,lut)&&!LUT_NOLOG){LUT_NOLOG=true;
    uiLog(t('LUT: видеокарта не приняла таблицу — превью без цвета'));}
  ctx.lkey=lut.path;
  gl.activeTexture(gl.TEXTURE0);}

// Кадр камеры с LUT: рисуем в скрытый WebGL2-canvas и отдаём его вместо видео.
// LUT нет, WebGL2 нет, кадр ещё не декодирован — возвращаем сам v (кадр как есть).
// Источник — <video> или готовая картинка кадра (lutWH): для drawImage и текстуры это
// один и тот же CanvasImageSource, разница только в том, где лежит размер кадра.
function lutApply(ci,v){
  const lut=lutFor(ci);
  if(!lut)return v;
  const ctx=lutGL();
  if(!ctx)return v;
  const wh=lutWH(v),vw=wh[0],vh=wh[1];
  if(!vw||!vh)return v;
  const gl=ctx.gl,size=lutSize(vw,vh),lw=size[0],lh=size[1];
  if(ctx.cv.width!==lw||ctx.cv.height!==lh){      // другая камера/разрешение кадра
    ctx.cv.width=lw;ctx.cv.height=lh;gl.viewport(0,0,lw,lh);ctx.vw=0;}
  try{
    gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,ctx.vtex);
    if(ctx.vw!==vw||ctx.vh!==vh){                 // размер кадра сменился — пересоздаём
      gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,v);
      ctx.vw=vw;ctx.vh=vh;}
    else gl.texSubImage2D(gl.TEXTURE_2D,0,0,0,gl.RGBA,gl.UNSIGNED_BYTE,v);
  }catch(e){
    if(!LUT_VFAIL){LUT_VFAIL=true;uiLog(t('LUT: кадр не лёг в текстуру — ')+e);}
    return v;}                                    // чужой источник: рисуем как есть
  lutTexLut(ctx,lut);
  gl.useProgram(ctx.prog);
  gl.uniform3fv(ctx.uMin,lut.domain_min||[0,0,0]);
  gl.uniform3fv(ctx.uMax,lut.domain_max||[1,1,1]);
  gl.uniform1f(ctx.uN,lut.size);
  gl.drawArrays(gl.TRIANGLES,0,3);
  return ctx.cv;}
