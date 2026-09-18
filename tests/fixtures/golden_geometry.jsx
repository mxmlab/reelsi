// SPDX-License-Identifier: AGPL-3.0-or-later
// Reelsi -> After Effects FULL build (auto-generated). Run: File>Scripts>Run Script File
(function () {
    // ===== STYLE / SHADOW (edit me) =====
    var FONT = "SFPro-CondensedSemibold";                       // PostScript-имя шрифта базового текста
    var HL_FONT = "SFPro-CondensedSemibold";                 // шрифт выделенных слов (жирный вариант)
    var HL_BOLD = false;                 // искусственный жирный (fauxBold) на выделенных
    var FONT_SIZE = 140, FILL = [1,1,1], POSY = 1145;
    var HL_FILL = [1,0.9176,0];                  // цвет выделения [r,g,b]
    var HL_RISE = 123, HL_DUR = 0.35;  // slide-up: снизу вверх на HL_RISE px за HL_DUR c
    var HL_STEP = 119.5;                 // шаг вертикальной стопки для подряд идущих жёлтых
    var HL_EASE_OUT = 35, HL_EASE_IN = 90;     // cubic-bezier(0.35,0.01,0.10,0.99)
    var SH_OPACITY = 68, SH_DIR = 181, SH_DIST = 5, SH_SOFT = 44;
    // вставки фото/видео — дефолты-средние из компа 1221 (правь при желании)
    var INS_MASK_R = 60;                                  // радиус скругления маски на прекомпе фото, px
    var INS_FX = "card";                              // "card" чёрная тень+скругление | "white" старый вид (белая тень+чокер)
    var INS_C1_ON2_X = 0, INS_C1_ON2_Y = 0;  // стиль кам1, попавший на перебивку: общий сдвиг точки покоя всех таких вставок, px
    var INS_C2_Y = 330;                           // Кам2: Y точки покоя вставки, px (считает Python: INS_C2_Y_FR * H)
    var INS_MASK_SQUARE_AR = 2.2;                         // фото уже этого отношения сторон режем маской в квадрат (ультравайды оставляем целиком)
    var INS_SH_OP = 49, INS_SH_DIR = 135, INS_SH_DIST = 15, INS_SH_SOFT = 70; // тень вставок: чёрная, opacity в % UI
    // Тайминги анимаций вставок (вход/выход, guard, noexit, пик наезда, вылет из-за
    // спины) считает Python и кладёт готовые ключи в ins.anim — в шаблоне их больше
    // не досчитываем: превью читает те же ключи из плана сцены (задание C)
    var TR_IN = 0.386, TR_SFX_LEAD = 0.083;    // Quick2 до стыка / whoosh ещё раньше
    // ====================================
    var W=1080, H=1920, FPS=60, DUR=112.3333;
    var CAM=[{"path":"C:\\footage\\cam1\\CLIP-006.MP4","name":"cam1","clips":[[0,443,960,1403,true,50.4],[443,651,1456,1664,true,50.4],[651,766,1741,1856,true,50.4],[766,856,1867,1957,true,50.4],[856,1609,2011,2764,true,50.4],[1609,1624,2773,2788,true,50.4],[1624,1645,2839,2860,true,50.4],[1645,1779,3066,3200,true,50.4],[1779,2054,3222,3497,true,50.4],[2054,2341,3613,3900,true,50.4],[2341,2412,3910,3981,true,50.4],[2412,2751,4005,4344,true,50.4],[2751,2921,4354,4524,true,50.4],[2921,3161,4554,4794,true,50.4],[3161,3422,4802,5063,true,50.4],[3422,3691,5084,5353,true,50.4],[3691,3750,5438,5497,true,50.4],[3750,3989,5524,5763,true,50.4],[3989,4604,5783,6398,true,50.4],[4604,4611,6416,6423,true,50.4],[4611,4860,6437,6686,true,50.4],[4860,5117,6741,6998,true,50.4],[5117,5192,7014,7089,true,50.4],[5192,5326,7118,7252,true,50.4],[5326,5653,7268,7595,true,50.4],[5653,5877,7681,7905,true,50.4],[5877,6307,7980,8410,true,50.4],[6307,6503,8723,8919,true,50.4],[6503,6740,8934,9171,true,50.4]]},{"path":"C:\\footage\\cam2\\CLIP-006.MP4","name":"cam2","clips":[[0,443,949,1392,false,50.4],[443,651,1445,1653,true,50.4],[651,766,1729,1844,true,50.4],[766,856,1856,1946,true,50.4],[856,1609,2000,2753,false,50.4],[1609,1624,2762,2777,true,50.4],[1624,1645,2828,2849,true,50.4],[1645,1779,3055,3189,false,50.4],[1779,2054,3211,3486,true,50.4],[2054,2341,3601,3888,true,50.4],[2341,2412,3899,3970,false,50.4],[2412,2751,3994,4333,true,50.4],[2751,2921,4342,4512,true,50.4],[2921,3161,4542,4782,false,50.4],[3161,3422,4791,5052,true,50.4],[3422,3691,5073,5342,true,50.4],[3691,3750,5427,5486,false,50.4],[3750,3989,5513,5752,true,50.4],[3989,4604,5772,6387,false,50.4],[4604,4611,6404,6411,true,50.4],[4611,4860,6425,6674,true,50.4],[4860,5117,6730,6987,false,50.4],[5117,5192,7002,7077,true,50.4],[5192,5326,7107,7241,true,50.4],[5326,5653,7256,7583,false,50.4],[5653,5877,7670,7894,true,50.4],[5877,6307,7969,8399,false,50.4],[6307,6503,8712,8908,true,50.4],[6503,6740,8922,9159,true,50.4]]}];        // [{path, clips:[[start,end,in,out,enabled,scale],...]}, ...]
    var SUBS=[[0,24,"\u041b\u041e\u0420\u0415\u041c",0,0,24],[24,35,"\u0421\u0423",0,0,35],[35,58,"\u0420\u0421*\u0418",0,0,58],[58,67,"\u0422",0,0,67],[67,89,"\u0422\u0415\u0422\u0423",0,0,89],[89,112,"\u0418\u041f\u0418\u0421\u0426",0,0,112],[114,142,"\u0413\u042d\u041b\u0418\u0422",0,0,142],[142,143,"\u0414",0,0,143],[143,158,"\u041e\u0414\u041b\u041e",0,0,158],[158,194,"\u0418\u041f\u0421\u0423\u041c\u0414\u041e\u041b\u041e",0,0,194],[194,233,"\u041b\u041e\u0420\u0421\u0418\u0422\u0410\u041c",0,0,233],[233,290,"\u041c\u0415\u0422\u041a\u041e\u041d\u0421\u0415\u041a\u0422",0,0,290],[290,320,"\u0415\u041a\u0422\u0415\u0422\u0423\u0420",0,0,320],[324,338,"\u0410\u0414\u0418\u041f\u0418\u0421\u0426",0,0,338],[338,355,"\u0418\u041d",0,0,355],[355,378,"\u0421\u0415\u0414\u0414\u041e",0,0,378],[378,385,"\u0421\u041c",0,0,385],[385,401,"\u0415\u041c\u0418\u041f",0,0,401],[401,426,"\u0414\u041e\u041b\u041e",0,0,426],[438,472,"\u0422\u0410\u041c\u0415\u0422\u041a",0,0,472],[472,492,"\u041d\u0421\u0415\u041a\u0422\u0415\u0422",0,0,492],[492,506,"\u0423\u0420\u0410\u0414\u0418\u041f",0,0,506],[506,522,"\u0421\u0426",0,0,522],[522,576,"\u0418\u0422\u0421\u0415\u0414\u0414\u041e\u042d\u042e\u0421\u041c\u041e",0,0,576],[576,587,"\u042d",0,0,587],[587,640,"\u041e\u0420\u0415\u041c\u0418\u041f\u0421\u0423\u041c\u0414\u041e",0,0,640],[643,661,"\u0423\u041c",0,0,661],[661,691,"\u0421\u0418\u0422\u0410\u041c\u0415",0,0,691],[691,718,"\u041a\u041e\u041d\u0421\u0415\u041a",0,0,718],[718,734,"\u0415\u0422",0,0,734],[734,744,"\u041f\u0418",0,0,744],[768,802,"\u042d\u041b\u0418\u0422\u0421\u0415\u0414\u0414",0,0,802],[802,841,"\u0414\u041e\u042d\u042e\u0421\u041c\u041e\u0414\u041b\u041e\u0420",0,0,841],[853,878,"\u0414\u041b\u041e\u0420\u0415\u041c\u0418\u041f\u0421\u0423\u041c",0,0,878],[878,913,"\u041f\u0421\u0423\u041c\u0414\u041e",0,0,913],[914,925,"\u041e\u0420\u0421",0,0,925],[925,952,"\u0415\u0422\u041a\u041e",0,0,952],[952,955,"\u041a\u0422",0,0,955],[955,979,"\u0414\u0418*\u041f\u0418",0,0,979],[983,998,"\u041d\u0413\u042d",0,0,998],[998,1025,"\u0415\u0414\u0414\u041e\u042d\u042e",0,0,1025],[1025,1051,"\u041c\u041e\u0414\u041b\u041e\u0420\u0415",0,0,1051],[1051,1105,"\u041c\u0418\u041f\u0421\u0423\u041c\u0414\u041e",0,0,1105],[1105,1142,"\u041e\u041b\u041e\u0420\u0421\u0418*\u0422\u0410\u041c\u0415\u0422",0,0,1142],[1152,1171,"\u0410\u041c",0,0,1171],[1171,1181,"\u0421\u0415\u041a",0,0,1181],[1181,1189,"\u0420\u0410",0,0,1189],[1189,1202,"\u0426\u0418\u041d\u0413",0,0,1202],[1202,1217,"\u0422\u0421*\u0415",0,0,1217],[1217,1243,"\u042e\u0421\u041c\u041e\u0414",0,0,1243],[1243,1252,"\u0420\u0415",0,0,1252],[1252,1278,"\u041c\u0414\u041e\u041b\u041e\u0420\u0421\u0418",0,0,1278],[1286,1313,"\u0418\u0422\u0410\u041c\u0415\u0422\u041a",0,0,1313],[1313,1393,"\u041e\u041d\u0421\u0415\u041a\u0422\u0415\u0422",0,0,1393],[1397,1410,"\u0422\u0423\u0420",0,0,1410],[1410,1457,"\u0418\u0421\u0426\u0418\u041d",0,0,1457],[1457,1488,"\u041b\u0418\u0422\u0421\u0415\u0414",0,0,1488],[1488,1516,"\u041e\u042d\u042e",0,0,1516],[1518,1596,"\u041b\u041e\u0420\u0415\u041c\u0418\u041f\u0421\u0423*\u041c\u0414\u041e\u041b\u041e\u0420\u0421\u0418\u0422",0,0,1596],[1602,1626,"\u0421",0,0,1626],[1626,1634,"\u0420\u0421",0,0,1634],[1640,1649,"\u0422\u041a\u041e",0,0,1649],[1649,1655,"\u0422\u0415",0,0,1655],[1655,1664,"\u0418\u041f\u0418\u0421",0,0,1664],[1664,1696,"\u0413\u042d\u041b\u0418\u0422\u0421\u0415\u0414\u0414",0,0,1696],[1696,1706,"\u0414\u0414\u041e\u042d",0,0,1706],[1706,1733,"\u041e\u0414\u041b\u041e\u0420\u0415\u041c",0,0,1733],[1733,1770,"\u0418\u041f\u0421\u0423\u041c\u0414",0,0,1770],[1770,1796,"\u041b\u041e\u0420\u0421\u0418\u0422",0,0,1796],[1796,1820,"\u041c\u0415\u0422\u041a",0,0,1820],[1820,1834,"\u0415\u041a\u0422\u0415\u0422\u0423\u0420\u0410\u0414\u0418",0,0,1834],[1834,1892,"\u0410\u0414\u0418\u041f\u0418\u0421",0,0,1892],[1896,1925,"\u0418\u041d\u0413\u042d",0,0,1925],[1925,1957,"\u0421\u0415\u0414\u0414\u041e\u042d\u042e\u0421\u041c\u041e",0,0,1957],[1957,1973,"\u0421",0,0,1973],[1973,2015,"\u0415\u041c\u0418\u041f\u0421\u0423\u041c\u0414\u041e\u041b",0,0,2015],[2015,2036,"\u0414\u041e",0,0,2036],[2047,2076,"\u0422\u0410\u041c\u0415\u0422\u041a",0,0,2076],[2076,2087,"\u041d\u0421\u0415",0,0,2087],[2087,2104,"\u0423\u0420\u0410\u0414\u0418\u041f",0,0,2104],[2104,2117,"90",0,0,2117],[2117,2143,"%",0,0,2143],[2143,2182,"\u042d\u042e\u0421\u041c\u041e\u0414",0,0,2182],[2182,2204,"\u041e\u0420\u0415\u041c\u0418",0,0,2204],[2204,2212,"\u0423",0,0,2212],[2212,2239,"\u0421\u0418\u0422\u0410\u041c\u0415",0,0,2239],[2239,2246,"\u041a\u041e",0,0,2246],[2246,2300,"\u0415\u0422\u0423\u0420\u0410\u0414\u0418\u041f\u0418\u0421",0,0,2300],[2384,2419,"\u041f\u0418",0,0,2419],[2428,2436,"\u042d\u041b",0,0,2436],[2436,2464,"\u0414\u041e\u042d\u042e\u0421\u041c\u041e\u0414",0,0,2464],[2472,2480,"\u0414\u041b",0,0,2480],[2480,2490,"\u041f\u0421\u0423",0,0,2490],[2490,2525,"\u041e\u0420\u0421\u0418\u0422\u0410\u041c",0,0,2525],[2525,2539,"\u0415\u0422\u041a\u041e",0,0,2539],[2539,2551,"\u041a\u0422",0,0,2551],[2551,2572,"\u0414\u0418\u041f\u0418\u0421",0,0,2572],[2572,2593,"\u041d\u0413\u042d*\u041b\u0418",0,0,2593],[2598,2610,"\u0415\u0414\u0414\u041e\u042d\u042e\u0421",0,0,2610],[2610,2624,"\u041c\u041e",0,0,2624],[2624,2645,"\u041c\u0418*\u041f\u0421",0,0,2645],[2645,2657,"\u041e",0,0,2657],[2657,2670,"\u0410\u041c\u0415",0,0,2670],[2670,2696,"\u0421\u0415\u041a\u0422\u0415\u0422\u0423\u0420",0,0,2696],[2696,2711,"\u0420\u0410",0,0,2711],[2711,2735,"\u0426\u0418\u041d\u0413\u042d\u041b\u0418",0,0,2735],[2743,2759,"\u0422\u0421",0,0,2759],[2759,2774,"\u042e\u0421\u041c\u041e\u0414\u041b",0,0,2774],[2774,2814,"\u0420\u0415\u041c\u0418\u041f\u0421\u0423",0,0,2814],[2814,2828,"\u041c\u0414\u041e\u041b",0,0,2828],[2828,2861,"\u0418\u0422\u0410\u041c\u0415\u0422\u041a\u041e",0,0,2861],[2861,2876,"\u041e\u041d",0,0,2876],[2876,2911,"\u0422\u0423\u0420\u0410\u0414\u0418",0,0,2911],[2915,2930,"\u0418",0,0,2930],[2930,2936,"\u041b",0,0,2936],[2936,2950,"\u041e\u042d\u042e\u0421\u041c",0,0,2950],[2950,2981,"\u041b\u041e\u0420\u0415\u041c\u0418\u041f\u0421",0,0,2981],[2981,3008,"\u0421\u0423\u041c\u0414\u041e\u041b\u041e\u0420\u0421",0,0,3008],[3008,3026,"\u0420\u0421\u0418\u0422\u0410\u041c\u0415\u0422\u041a\u041e",0,0,3026],[3026,3067,"\u0422\u041a\u041e\u041d\u0421\u0415",0,0,3067],[3067,3114,"\u0422\u0415\u0422\u0423\u0420\u0410\u0414\u0418\u041f\u0418",0,0,3114],[3114,3142,"\u0418\u041f\u0418\u0421\u0426",0,0,3142],[3151,3172,"\u0413\u042d\u041b",0,0,3172],[3172,3184,"\u0414\u0414",0,0,3184],[3184,3217,"\u041e\u0414\u041b\u041e\u0420\u0415\u041c\u0418",0,0,3217],[3217,3241,"\u0418\u041f\u0421",0,0,3241],[3245,3278,"\u041b\u041e\u0420\u0421",0,0,3278],[3278,3294,"\u041c\u0415",0,0,3294],[3294,3320,"\u0415\u041a\u0422\u0415\u0422\u0423\u0420\u0410",0,0,3320],[3320,3342,"\u0410\u0414\u0418",0,0,3342],[3342,3377,"\u0418\u041d\u0413\u042d\u041b\u0418\u0422",0,0,3377],[3377,3384,"\u0421",0,0,3384],[3384,3406,"\u0421\u041c\u041e\u0414\u041b\u041e",0,0,3406],[3406,3422,"\u0415\u041c",0,0,3422],[3422,3438,"\u0414\u041e",0,0,3438],[3438,3462,"\u0422\u0410\u041c\u0415\u0422",0,0,3462],[3462,3472,"\u041d\u0421\u0415",0,0,3472],[3472,3503,"\u0423\u0420\u0410\u0414\u0418\u041f\u0418",0,0,3503],[3503,3644,"\u0421\u0426\u0418\u041d\u0413\u042d\u041b\u0418",0,0,3644],[3644,3670,"\u0418\u0422\u0421\u0415\u0414\u0414",0,0,3670],[3670,3686,"\u042d\u042e\u0421\u041c",0,0,3686],[3686,3736,"\u041e\u0420\u0415\u041c\u0418\u041f\u0421\u0423\u041c\u0414",0,0,3736],[3738,3761,"\u0423\u041c\u0414\u041e\u041b",0,0,3761],[3761,3774,"\u0421\u0418",0,0,3774],[3774,3794,"\u041a\u041e*\u041d\u0421",0,0,3794],[3794,3821,"\u0415\u0422\u0423*\u0420\u0410",0,0,3821],[3826,3842,"\u041f\u0418",0,0,3842],[3842,3880,"\u042d\u041b\u0418\u0422\u0421\u0415\u0414\u0414\u041e\u042d",0,0,3880],[3880,3932,"\u0414\u041e\u042d\u042e\u0421\u041c\u041e\u0414",0,0,3932],[3932,3964,"\u0414\u041b\u041e\u0420\u0415\u041c\u0418\u041f",0,0,3964],[3964,3991,"\u041f",0,0,3991],[3991,4030,"\u041e\u0420\u0421\u0418*\u0422\u0410\u041c\u0415",0,0,4030],[4030,4048,"\u0415\u0422",0,0,4048],[4048,4073,"320",0,0,4073],[4073,4099,"\u0414\u0418",0,0,4099],[4099,4132,"640",0,0,4132],[4132,4178,"\u0415\u0414",0,0,4178],[4178,4202,"\u041c",0,0,4202],[4202,4224,"\u041c\u0418\u041f\u0421\u0423",0,0,4224],[4225,4236,"\u041e\u041b",0,0,4236],[4236,4259,"\u0410\u041c\u0415\u0422\u041a",0,0,4259],[4259,4290,"\u0421\u0415\u041a\u0422\u0415\u0422\u0423\u0420\u0410",0,0,4290],[4290,4308,"\u0420\u0410\u0414\u0418\u041f\u0418\u0421",0,0,4308],[4308,4324,"5",0,0,4324],[4324,4343,"\u0422\u0421\u0415\u0414\u0414",0,0,4343],[4343,4378,"\u042e\u0421\u041c\u041e\u0414\u041b\u041e\u0420\u0415",0,0,4378],[4378,4391,"\u0420\u0415\u041c\u0418\u041f",0,0,4391],[4391,4405,"\u041c",0,0,4405],[4405,4426,"\u0418\u0422\u0410\u041c",0,0,4426],[4428,4472,"\u041e\u041d\u0421\u0415\u041a\u0422",0,0,4472],[4472,4496,"\u0422\u0423\u0420\u0410\u0414\u0418\u041f\u0418",0,0,4496],[4496,4512,"\u0418\u0421\u0426",0,0,4512],[4512,4550,"\u041b\u0418\u0422\u0421\u0415\u0414\u0414",0,0,4550],[4550,4562,"\u041e\u042d\u042e",0,0,4562],[4562,4595,"\u041b\u041e\u0420\u0415\u041c\u0418\u041f",0,0,4595],[4600,4630,"\u0421\u0423\u041c\u0414\u041e\u041b",0,0,4630],[4630,4654,"\u0420\u0421\u0418\u0422\u0410\u041c\u0415\u0422\u041a\u041e\u041d",0,0,4654],[4654,4676,"\u0422\u041a\u041e\u041d\u0421",0,0,4676],[4685,4700,"\u0422\u0415\u0422",0,0,4700],[4700,4732,"\u0418\u041f\u0418\u0421\u0426\u0418",0,0,4732],[4732,4735,"\u0413",0,0,4735],[4735,4772,"\u0414\u0414\u041e*\u042d\u042e\u0421",0,0,4772],[4772,4782,"\u041e",0,0,4782],[4782,4837,"\u0418\u041f\u0421\u0423\u041c\u0414\u041e\u041b\u041e\u0420",0,0,4837],[4841,4870,"\u041b\u041e\u0420\u0421",0,0,4870],[4870,4897,"\u041c\u0415\u0422\u041a\u041e",0,0,4897],[4897,4931,"\u0415\u041a\u0422\u0415\u0422\u0423\u0420\u0410\u0414\u0418\u041f\u0418",0,0,4931],[4946,4956,"\u0410",0,0,4956],[4956,4968,"\u0418\u041d",0,0,4968],[4968,4986,"\u0421\u0415\u0414\u0414\u041e\u042d\u042e\u0421",0,0,4986],[4986,5021,"\u0421\u041c\u041e\u0414\u041b*\u041e\u0420\u0415\u041c",0,0,5021],[5021,5035,"\u0415\u041c\u0418",0,0,5035],[5035,5058,"\u0414\u041e\u041b\u041e\u0420\u0421\u0418\u0422\u0410\u041c",0,0,5058],[5058,5090,"\u0422\u0410\u041c\u0415\u0422\u041a",0,0,5090],[5090,5100,"\u041d\u0421\u0415\u041a\u0422",0,0,5100],[5100,5138,"\u0423\u0420\u0410\u0414\u0418\u041f\u0418",0,0,5138],[5138,5156,"\u0421\u0426\u0418\u041d",0,0,5156],[5156,5261,"\u0418\u0422\u0421\u0415\u0414\u0414",0,0,5261],[5261,5278,"\u042d\u042e\u0421\u041c\u041e",0,0,5278],[5278,5314,"\u041e\u0420\u0415\u041c",0,0,5314],[5316,5354,"\u0423\u041c\u0414\u041e\u041b\u041e\u0420\u0421\u0418\u0422\u0410",0,0,5354],[5354,5392,"\u0421\u0418\u0422\u0410\u041c",0,0,5392],[5392,5402,"\u041a\u041e",0,0,5402],[5402,5419,"500",0,0,5419],[5419,5443,"\u041f\u0418",0,0,5443],[5443,5466,"\u042d",0,0,5466],[5466,5479,"\u0414\u041e\u042d\u042e",0,0,5479],[5488,5514,"\u0414\u041b\u041e\u0420\u0415",0,0,5514],[5514,5545,"\u041f\u0421\u0423\u041c\u0414\u041e\u041b\u041e\u0420\u0421",0,0,5545],[5545,5575,"\u041e\u0420\u0421*\u0418\u0422",0,0,5575],[5575,5582,"\u0415",0,0,5582],[5582,5616,"\u041a\u0422\u0415\u0422\u0423\u0420\u0410\u0414",0,0,5616],[5616,5641,"\u0414\u0418\u041f\u0418\u0421",0,0,5641],[5646,5663,"\u041d\u0413\u042d\u041b",0,0,5663],[5663,5689,"\u0415\u0414\u0414\u041e\u042d\u042e\u0421\u041c\u041e",0,0,5689],[5689,5706,"\u041c\u041e\u0414\u041b",0,0,5706],[5712,5725,"\u041c\u0418\u041f",0,0,5725],[5725,5735,"\u041e\u041b\u041e\u0420\u0421\u0418",0,0,5735],[5735,5749,"\u0410\u041c",0,0,5749],[5749,5777,"\u0421\u0415\u041a\u0422\u0415\u0422\u0423",0,0,5777],[5777,5827,"\u0420\u0410\u0414\u0418\u041f*\u0418\u0421\u0426\u0418",0,0,5827],[5827,5857,"\u0426\u0418\u041d\u0413\u042d\u041b\u0418\u0422\u0421\u0415",0,0,5857],[5868,5881,"\u0422\u0421",0,0,5881],[5881,5891,"\u042e",0,0,5891],[5891,5917,"\u0420\u0415\u041c\u0418",0,0,5917],[5917,5945,"\u041c\u0414\u041e\u041b\u041e\u0420\u0421",0,0,5945],[5945,5948,"\u0418\u0422",0,0,5948],[5948,5974,"\u041e\u041d\u0421\u0415\u041a\u0422\u0415",0,0,5974],[5974,6006,"\u0422\u0423\u0420\u0410\u0414\u0418\u041f",0,0,6006],[6006,6036,"\u0418\u0421\u0426\u0418\u041d\u0413\u042d\u041b",0,0,6036],[6036,6041,"\u041b",0,0,6041],[6041,6067,"\u041e\u042d\u042e\u0421\u041c\u041e\u0414",0,0,6067],[6067,6089,"\u041b\u041e\u0420\u0415\u041c\u0418\u041f",0,0,6089],[6089,6127,"\u0421\u0423\u041c\u0414\u041e\u041b\u041e\u0420\u0421",0,0,6127],[6127,6137,"\u0420",0,0,6137],[6137,6166,"\u0422\u041a\u041e\u041d\u0421\u0415\u041a\u0422",0,0,6166],[6166,6172,"\u0422",0,0,6172],[6172,6204,"\u0418\u041f\u0418\u0421\u0426\u0418\u041d\u0413\u042d",0,0,6204],[6204,6244,"\u0413\u042d\u041b\u0418\u0422\u0421",0,0,6244],[6244,6256,"\u0414\u0414\u041e\u042d",0,0,6256],[6256,6288,"\u041e\u0414\u041b\u041e\u0420",0,0,6288],[6295,6324,"\u0418\u041f\u0421\u0423\u041c\u0414\u041e",0,0,6324],[6324,6360,"\u041b\u041e\u0420\u0421*\u0418\u0422\u0410\u041c",0,0,6360],[6360,6368,"\u041c",0,0,6368],[6368,6398,"\u0415\u041a\u0422\u0415\u0422\u0423\u0420\u0410",0,0,6398],[6398,6428,"\u0410\u0414\u0418\u041f\u0418\u0421\u0426\u0418\u041d",0,0,6428],[6430,6445,"\u0418\u041d\u0413\u042d\u041b\u0418\u0422",0,0,6445],[6445,6460,"\u0421\u0415\u0414\u0414\u041e",0,0,6460],[6460,6492,"\u0421\u041c\u041e\u0414\u041b\u041e\u0420",0,0,6492],[6494,6509,"\u0415",0,0,6509],[6509,6541,"\u0414\u041e\u041b\u041e\u0420\u0421\u0418\u0422",0,0,6541],[6541,6546,"\u0422",0,0,6546],[6546,6564,"\u041d\u0421\u0415\u041a\u0422\u0415\u0422\u0423",0,0,6564]];       // [[start,end,"WORD",hl,row,gend], ...] hl=1 жёлтое, row=ряд стопки, gend=общий конец
    var POP="";         // SFX «поп» для жёлтых слов или ""
    var CENSOR=[[0.775,0.8708],[16.0767,16.1567],[18.725,18.7764],[20.1583,20.2208],[25.9158,25.9842],[43.0417,43.1],[43.8733,43.9433],[63.0333,63.1],[63.4583,63.5333],[66.8056,66.8778],[79.181,79.269],[83.3917,83.45],[92.6667,92.75],[96.7,96.7833],[105.6667,105.7333]];   // [[start,end], ...] сек — окна мьюта голоса (плохие слова)
    var EXPOSURE=0;  // яркость: Lumetri Exposure на все клипы камер (0 = не вешать)
    var ROTO=[];          // [{ci,ts,te,cs,scale,mf,mask}, ...] — сплошное рото персонажа по видимой камере (весь хрон); mf = маска мельче исходника в mf раз
    var INTRO_GROUPS=[[]];  // [[{words,color,times},...], ...] — интро по прекомпам (кросс-фейд между группами)
    var INTRO_FONT="SFPro-CondensedSemibold", INTRO_HL_FONT="SFPro-CondensedSemibold";  // шрифты интро (обычный/выделение); по умолч. = как субтитры
    var INTRO_MODE="word";  // "word" пословно (слой на слово) | "line" построчно (слой на строку, раскладка AE)
    var INTRO_GLOW=1;  // Glow Intensity на интро-тексте (AE-дефолт 1.0)
    var INTRO_SCALE=100, INTRO_Y=0;  // общий масштаб (%) и сдвиг по вертикали (px) ВСЕГО
                                    // интро: висят на нуле «интро», то есть двигают/масштабируют все прекомпы разом.
                                    // Опускание под INTRO_SAFE_TOP считает Python (задание Q2) — здесь только поправка Scale.
    var INTRO_ON2=[0];    // [0|1 на группу] — группа появляется на перебивке (Камера 2): свой нул
    var INTRO_IDY=[0.0];    // [px на группу] — опускание блока под INTRO_SAFE_TOP, считает Python (задание Q2)
    var INTRO_Y2=0;      // сдвиг по вертикали (px) нула «интро на кам2» ПОВЕРХ INTRO_Y:
                                    // на перебивке кадр другой, и текст за спиной просится ниже
    var INTRO_WIDE=3;               // ширина интро-прекомпа в долях кадра: прекомп шире кадра, чтобы
                                    // размер текста поджимался СКАЛОЙ СЛОЯ в мастере, не заходя в композ.
                                    // Текст внутри всегда раскладывается в полный кегль — ужимание
                                    // длинных строк (автофит) считает Python в плане (задание BP)
    var INSERTS=[{"t":"photo","style":"cam1","media":"C:/x/a.png","start":1,"end":3,"scale":50,"mosaic":false,"x":0,"y":0,"sc":100,"mw":100,"mh":100,"sin":0,"noexit":false,"front":true,"oncam2":false,"en":0.38,"ex":0.47,"anim":{"position":[[1,[0,464.7]],[1.5,[0,-567]],[2.5,[0,-567]],[3,[0,464.7]]]}},{"t":"photo","style":"cam2","media":"C:/x/cam2.png","start":8,"end":9.5,"scale":44,"mosaic":false,"x":0,"y":0,"sc":100,"mw":100,"mh":100,"sin":0,"noexit":false,"front":true,"oncam2":false,"en":0.38,"ex":0.47,"anim":{"scale":[[8,100],[8.38,44],[9.03,44],[9.5,100]],"opacity":[[8,0],[8.38,100],[9.03,100],[9.5,0]],"blur":[[8,41.0],[8.38,0.0],[9.03,0.0],[9.5,41.0]]}},{"t":"photo","style":"cam1","media":"C:/x/b.png","start":5,"end":7.3833,"scale":50,"mosaic":false,"x":0,"y":0,"sc":100,"mw":100,"mh":100,"sin":0,"noexit":true,"front":true,"oncam2":false,"en":0.38,"ex":0.47,"anim":{"position":[[5,[0,464.7]],[5.5,[0,-567]],[7.3833,[0,-567]]]}}]; // [{t:"photo"|"video",style:"cam2"|"cam1",media,start,end,scale,sc,mw,mh,x,y,front,oncam2}, ...] x/y = сдвиг точки покоя, px; sc = ручной масштаб в % от авто (mw/mh — форма маски); front=видео перед человеком; oncam2=стиль кам1, но в кадре перебивка
    var SUB_HIDE=[];  // [[t, opacity], ...] — уход субтитров на rise-вставках
    var TRANS="", TRANS_SFX="";  // Quick 2.mov + whoosh для видеовставок
    var CAM1_SCALE=[[0,182],[62,100],[856,136.9],[918,100],[1645,126.7],[1707,100],[2341,136.6],[2403,100],[2921,125.3],[2983,100],[3691,133.7],[3753,100],[3989,115.9],[4051,100],[4860,128.7],[4922,100],[5326,115.2],[5388,100],[5877,138.9],[5939,100]];  // [[frame, percent], ...] зум Null камеры 1 — правь/очисти под видео
    var CAM1_HOLDS=[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0];  // [0/1, ...] отрезок от ключа до следующего: 1=HOLD (значение держится), 0=BEZIER (плавно)
    var CAM1_FIT=100;     // масштаб кадра Камеры 1 в % ЗАПОЛНЕНИЯ КОМПОЗИЦИИ при зуме нула 100%:
                                   // 100 = кадр заполнен ровно, 120 = врезка на 20%. Считается от РЕАЛЬНОГО
                                   // размера исходника (AE его знает), а не от масштаба из Премьера — тот
                                   // врёт, если файл пережали: 1080p-исходник приезжал со scale=50.4 и
                                   // вставал вполовину кадра. Рото-копия едет следом. Зум нула — поверх.
    var MUSIC="", MUSIC_DB=-20;  // музыка отдельным аудиослоем, уровень в dB
    var VOICE_DB=0;                    // базовая громкость голоса (камера 1); цензура ныряет отсюда в −100
    var AUDIO_FADE=0.01;                // сек: микро-фейд громкости на краях каждого аудио-клипа (0 = выкл)
    var RISER="";                          // интро-SFX (ризер) или ""
    var DISCLAIMER="", DISC_END=1.35, DISC_SIZE=47, DISC_Y=1466;

    app.beginUndoGroup("Reelsi build");
    // Лог сборки. Файл .aelog.txt заводит ХВОСТ (задание CD), а ошибки бывают раньше него —
    // копим и сливаем в файл после открытия. Пустых catch в шаблоне нет: молча терять
    // причину нельзя — так кривая наезда не применялась на всех ключах, и никто не узнал.
    var _log = null, _pending = [];
    function _LOG(msg){
        if (_log){ try{ _log.writeln(msg); }catch(e){} }
        else { _pending.push(msg); $.writeln("[reelsi] " + msg); }  // ручной режим: видно в консоли
    }
    // Шрифт по PostScript-имени. У AE бывает НЕСКОЛЬКО записей с одним именем (след
    // переустановки шрифта), и выбор по имени встаёт на битую: в тексте остаётся Times,
    // а try/catch молчит — ошибки нет, шрифт просто не тот (задание ZF).
    // Перебираем копии через fontObject и кэшируем выбор на имя — проба один раз на имя.
    var _FONT_PICK = {};   // PostScript-имя -> Font (рабочая копия) | null (ставить по имени)
    var _fontProbe = null, _fontProbeSp = null;   // пробная композиция со слоем — одна на весь .jsx
    function _fontPick(ps){
        if (_FONT_PICK[ps] !== undefined) return _FONT_PICK[ps];
        var pick = null, list = null, can = false;
        try{ can = !!(app.fonts && app.fonts.getFontsByPostScriptName); }catch(e){ _LOG("app.fonts: " + e); }
        if (can){   // AE < 24: метода нет — ставим по имени, пробы не делаем
            try{ list = app.fonts.getFontsByPostScriptName(ps); }catch(e){ _LOG("getFontsByPostScriptName(«"+ps+"»): " + e); }
            try{
                if (!_fontProbe){
                    _fontProbe = app.project.items.addComp("__reelsi_font_probe", 100, 100, 1.0, 1, 25);
                    var pl = _fontProbe.layers.addText("Reelsi");
                    _fontProbeSp = pl.property("ADBE Text Properties").property("ADBE Text Document");
                }
                if (list && list.length > 1){   // двоятся ИМЕНА, а не шрифты: одна копия — не тот случай
                    for (var i=0; i<list.length; i++){
                        var d = _fontProbeSp.value; d.fontObject = list[i]; _fontProbeSp.setValue(d);
                        if (_fontProbeSp.value.font === ps){ pick = list[i]; break; }
                    }
                }
                if (!pick){   // копия одна или ни одна не встала — проба по имени
                    var d2 = _fontProbeSp.value; d2.font = ps; _fontProbeSp.setValue(d2);
                    if (_fontProbeSp.value.font !== ps)
                        _LOG("шрифт " + ps + " не принят After Effects — будет шрифт по умолчанию");
                }
            }catch(e){ _LOG("проба шрифта «"+ps+"»: " + e); }
        }
        _FONT_PICK[ps] = pick;
        return pick;
    }
    function setFont(d, ps){ var o = _fontPick(ps); if (o) d.fontObject = o; else d.font = ps; }
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
    function imp(p){ var f=new File(p); if(!f.exists){alert("Не найден файл:\n"+p); return null;}
        for(var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
            if((it instanceof FootageItem) && it.mainSource && it.mainSource.file
               && it.mainSource.file.fsName==f.fsName) return it; }
        return app.project.importFile(new ImportOptions(f)); }
    // КВАРТЕР-качество на ВСЕХ видеослоях сборки (камеры, видеовставки, рото-копии,
    // переходы): многослойные 4K-проекты в полном качестве жмут таймлайн и рендер,
    // а тянуть каждый клип в Draft руками после сборки — та же рутина на каждый ролик
    function draftQ(l){ try{ l.quality = LayerQuality.DRAFT; }catch(e){ try{ l.quality = 0.25; }catch(e2){} } }

    // бины в панели проекта: переиспользуем уже существующую папку с таким именем, иначе создаём
    var _bins={};
    function bin(n){ if(_bins[n]===undefined){ var f=null;
        for(var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
            if((it instanceof FolderItem) && it.name==n){ f=it; break; } }
        if(!f){ try{ f=app.project.items.addFolder(n); }catch(e){ f=null; } } _bins[n]=f; } return _bins[n]; }
    function toBin(item,n){ if(item){ try{ var f=bin(n); if(f && item.parentFolder!==f) item.parentFolder=f; }catch(e){} } }

    // 2 кейфрейма -> кривая cubic-bezier(0.35,0.01,0.10,0.99): key1 out-influence 35, key2 in-influence 90
    function easePair(prop){
        for (var k=1;k<=prop.numKeys;k++)
            prop.setInterpolationTypeAtKey(k, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
        if (prop.numKeys<2) return;
        temporalEase(prop, HL_EASE_IN, HL_EASE_OUT);   // Position 1-мерна — откат внутри (задание CE)
    }

    var main = app.project.items.addComp("CLIP-006", W, H, 1.0, Math.max(DUR,1), FPS);

    // ---- intro: riser SFX + disclaimer text (fades out) ----
    if (RISER){ var rf=imp(RISER); if(rf){ toBin(rf,"Интро"); var rl=main.layers.add(rf); rl.name="Интро SFX"; rl.startTime=0; } }
    if (DISCLAIMER){
        var dl=main.layers.addText(DISCLAIMER);
        var dsp=dl.property("ADBE Text Properties").property("ADBE Text Document");
        var dd=dsp.value; dd.resetCharStyle(); dd.resetParagraphStyle(); dd.text=DISCLAIMER;
        try{setFont(dd, FONT);}catch(e){} dd.fontSize=DISC_SIZE; dd.fillColor=[1,1,1]; dd.applyFill=true;
        try{dd.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}
        dsp.setValue(dd);
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
    var subc = app.project.items.addComp("Субтитры (CLIP-006)", SW, H, 1.0, Math.max(DUR,1), FPS);
    toBin(subc,"Субтитры");                     // в корне — только основные композиции
    var FITW = W*0.92;                          // длинные слова ужимаем под эту ширину
    for (var i=0;i<SUBS.length;i++){
        var sw=SUBS[i], hl=sw[3], row=(sw[4]|0);
        var L = subc.layers.addText(sw[2]);
        var sp = L.property("ADBE Text Properties").property("ADBE Text Document");
        var d = sp.value; d.resetCharStyle(); d.resetParagraphStyle(); d.text=sw[2];
        try{setFont(d, (hl?HL_FONT:FONT));}catch(e){ try{setFont(d, FONT);}catch(e2){} }   // жёлтый шрифт не найден -> база (не дефолт AE)
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
        }
    }
    // ---- поп-SFX на каждое жёлтое слово (в момент появления) ----
    if (POP){ var popItem=imp(POP);
        if (popItem){ for (var pi=0; pi<SUBS.length; pi++){ if (SUBS[pi][3]){
            var pl=main.layers.add(popItem); pl.name="Поп"; pl.startTime=(SUBS[pi][0]+4)/FPS;  // +4 кадра — звук лучше ложится
            try{ pl.outPoint=pl.startTime+0.1; }catch(e){}          // поп обрезан до ~0.1с
            try{ pl.property("ADBE Audio Group").property("ADBE Audio Levels").setValue([-8,-8]); }catch(e){} } } } }  // тише

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
    var cam1null = nulls[0];
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
    //  за собой её зум-дрейф 100-160%, хотя самой Камеры 1 в кадре в этот момент нет — фото
    //  необъяснимо ездило и меняло размер. Двигая этот нул, правишь сразу все такие вставки.
    var insNull1b = main.layers.addNull(Math.max(DUR,1)); insNull1b.name="вставки кам1 на кам2"; insNull1b.enabled=false;
    insNull1b.property("ADBE Transform Group").property("ADBE Position").setValue([W/2,H/2]);
    // «интро» — ОДИН общий нул для всех интро-прекомпов, привязан к Null Камеры 1 (следует за её зумом)
    var introNull = main.layers.addNull(Math.max(DUR,1)); introNull.name="интро"; introNull.enabled=false;
    if(cam1null){ introNull.parent=cam1null;
        introNull.property("ADBE Transform Group").property("ADBE Position").setValue([0,INTRO_Y]); }
    else introNull.property("ADBE Transform Group").property("ADBE Position").setValue([W/2,H/2+INTRO_Y]);
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
        introNull2.property("ADBE Transform Group").property("ADBE Position").setValue([0,INTRO_Y+INTRO_Y2]); }
    else introNull2.property("ADBE Transform Group").property("ADBE Position").setValue([W/2,H/2+INTRO_Y+INTRO_Y2]);
    if (INTRO_SCALE!=100)
        try{ introNull2.property("ADBE Transform Group").property("ADBE Scale").setValue([INTRO_SCALE,INTRO_SCALE]); }catch(e){}

    // optional zoom animation on Camera-1 Null
    if (cam1null && CAM1_SCALE.length){
        var sc = cam1null.property("ADBE Transform Group").property("ADBE Scale");
        var CAM1_EASE=[[33.3333,35],[90,33.3333],[33.3333,35],[90,33.3333],[33.3333,35],[90,33.3333],[33.3333,35],[90,33.3333],[33.3333,35],[90,33.3333],[33.3333,35],[90,33.3333],[33.3333,35],[90,33.3333],[33.3333,35],[90,33.3333],[33.3333,35],[90,33.3333],[33.3333,35],[90,33.3333]];  // [[in,out], ...] влияние ease на КАЖДЫЙ ключ — посчитано в Python
        // 1) setValueAtTime всех ключей
        for (var z=0; z<CAM1_SCALE.length; z++)
            sc.setValueAtTime(CAM1_SCALE[z][0]/FPS, [CAM1_SCALE[z][1], CAM1_SCALE[z][1]]);
        // 2) всем ключам BEZIER/BEZIER
        for (var kb=1; kb<=sc.numKeys; kb++)
            sc.setInterpolationTypeAtKey(kb, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
        // 3) temporalEase(sc, eIns, eOuts) на все ключи
        var eIns=[], eOuts=[];
        for (var z2=0; z2<sc.numKeys; z2++){
            var ee=CAM1_EASE[z2]||[33.3333,33.3333];
            eIns.push(ee[0]); eOuts.push(ee[1]);
        }
        // Scale 2D-нула: value.length=2, а AE ждёт 1 — откат внутри, ошибка не прячется (задание CE)
        temporalEase(sc, eIns, eOuts);
        // 4) затем для каждого ключа k (1-based): in / out HOLD или BEZIER по CAM1_HOLDS
        for (var k=1; k<=sc.numKeys; k++){
            var inHold = (k > 1 && CAM1_HOLDS[k - 2]) ? KeyframeInterpolationType.HOLD : KeyframeInterpolationType.BEZIER;
            var outHold = (CAM1_HOLDS[k - 1]) ? KeyframeInterpolationType.HOLD : KeyframeInterpolationType.BEZIER;
            if (inHold === KeyframeInterpolationType.HOLD || outHold === KeyframeInterpolationType.HOLD)
                sc.setInterpolationTypeAtKey(k, inHold, outHold);
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
        if(transItem){  var tl=main.layers.add(transItem);  tl.name="Переход"; tl.startTime=cut-TR_IN;
            draftQ(tl);
            try{ tl.blendingMode=BlendingMode.ADD; }catch(e){}     // Quick2 наложением Add
            transLayers.push(tl); }
        if(whooshItem){ var wl=main.layers.add(whooshItem); wl.name="Whoosh"; wl.startTime=cut-TR_IN-TR_SFX_LEAD;
            try{ wl.property("ADBE Audio Group").property("ADBE Audio Levels").setValue([-10,-10]); }catch(e){} } }  // whoosh тише
    function dropShadow(L, op){                    // белая тень интро-текста (не зависит от стиля вставок INS_FX)
        var ds=addFX(L,"ADBE Drop Shadow");
        setP(ds,"ADBE Drop Shadow-0001",[1,1,1]); setP(ds,"ADBE Drop Shadow-0002",op);
        setP(ds,"ADBE Drop Shadow-0003",135); setP(ds,"ADBE Drop Shadow-0004",0); setP(ds,"ADBE Drop Shadow-0005",287); }
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
        if (INS_FX!="white"){                          // маска-скругление только у нового вида
            var ph=H; try{ if(pit.width&&pit.height) ph=pit.height*(W/pit.width); }catch(e){}   // высота фото в прекомпе (тянуто под ширину)
            var mh=ph, mw=W;
            // квадратная карточка: режем по меньшей стороне. Ультравайд (артерия, схемы) в квадрат
            // не лезет — теряется смысл картинки, такие оставляем целиком по ширине.
            if (mh>0 && W/mh <= INS_MASK_SQUARE_AR){ var side=Math.min(W, mh); mw=side; mh=side; }
            // ручная правка формы карточки (поля «Маска Ш/В» в UI, % от авто): авторасчёт
            // квадратит всё подряд, а у половины картинок предмет в квадрат не помещается.
            // Больше самого фото маску не растягиваем — за его краем в прекомпе пусто.
            mw = Math.max(20, Math.min(W,  mw*(ins.mw||100)/100));
            mh = Math.max(20, Math.min(ph, mh*(ins.mh||100)/100));
            roundMask(L, (W-mw)/2, Math.max(0,(H-mh)/2), (W+mw)/2, Math.min(H,(H+mh)/2), INS_MASK_R); }
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
            insFX(L,"cam1");
            L.name="Вставка: "+fname;                  // рото теперь сплошное — спец-метка не нужна
        } else {                                        // Камера 2: scale + blur + opacity
            L.name="Вставка: "+fname;
            if(insNull2) L.parent=insNull2;             // общий контроллер вставок кам2 (нул в мировом начале)
            // две cam2-вставки в одном окне раньше разводили по X (верхняя прятала нижнюю) —
            // теперь просто ложатся друг на друга, верхняя (позже добавленная) перекрывает нижнюю
            L.property("ADBE Transform Group").property("ADBE Position").setValue([W/2+ix, INS_C2_Y+iy]);
            applyKeyframes(L.property("ADBE Transform Group").property("ADBE Position"),
                           ins.anim && ins.anim.position);
            addMosaic(L, ins.mosaic);
            insFX(L,"cam2");
            if(ins.anim && ins.anim.blur){
                var bl=addFX(L,"ADBE Box Blur2");
                if(bl){ setP(bl,"ADBE Box Blur2-0004",0);    // Repeat Edge Pixels всегда выкл
                    applyKeyframes(bl.property("ADBE Box Blur2-0001"), ins.anim.blur); }
            }
            // наезд считаем ОТ осевшего масштаба (PEAK/BASE ≈ 2.27×), а не константой 100:
            // у крупной карточки (sc>227%) фиксированный пик оказывался МЕНЬШЕ конечного
            // размера — вместо наезда вставка раздувалась внутрь кадра. Ключи наезда,
            // opacity и блюра посчитал Python — план сцены (задание C)
            applyKeyframes(L.property("ADBE Transform Group").property("ADBE Scale"),
                           ins.anim && ins.anim.scale, true);
            applyKeyframes(L.property("ADBE Transform Group").property("ADBE Opacity"),
                           ins.anim && ins.anim.opacity);
        }
        try{ L.property("ADBE Transform Group").property("ADBE Position").expression="wiggle(1,15)"; }catch(e){}  // лёгкое дрожание
    }

    // ---- интро-текст: по прекомпу на группу строк; между группами кросс-фейд по opacity ----
    var introLayers = [];
    if (INTRO_GROUPS.length){
        var LINE_STEP=160, F_DUR=0.3, HOLD=1.0, F_OUT=0.75, F_FADE=0.35;
        function introDoc(tl, txt, col){
            var sp=tl.property("ADBE Text Properties").property("ADBE Text Document");
            var dd=sp.value; dd.resetCharStyle(); dd.resetParagraphStyle(); dd.text=""+txt;
            try{setFont(dd, (col=="yellow"?INTRO_HL_FONT:INTRO_FONT));}catch(e){ try{setFont(dd, INTRO_FONT);}catch(e2){} }
            try{dd.fauxBold=(col=="yellow"&&HL_BOLD);}catch(e){} dd.fontSize=FONT_SIZE;
            dd.fillColor=(col=="yellow"?HL_FILL:[1,1,1]); dd.applyFill=true;
            try{dd.justification=ParagraphJustification.CENTER_JUSTIFY;}catch(e){}
            sp.setValue(dd);
        }
        function introW(tl){ try{ return tl.sourceRectAtTime(0,false).width; }catch(e){ return 0; } }
        for (var gI=0; gI<INTRO_GROUPS.length; gI++){
            var GRP=INTRO_GROUPS[gI]; if(!GRP.length) continue;
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
            var nL=GRP.length, cY=H/2 - (nL-1)/2*LINE_STEP, maxLineW=0;
            for (var qi=0; qi<nL; qi++){
                var ln=GRP[qi], wds=ln.words||[], tms=ln.times||[], lineY=cY+qi*LINE_STEP;
                if(!wds.length) continue;
                if (INTRO_MODE=="line"){                        // одна строка = один слой (раскладка AE), фейд по 1-му слову
                    var Ll=ic.layers.addText(""); introDoc(Ll, wds.join(" "), ln.color);
                    if(introW(Ll)>maxLineW) maxLineW=introW(Ll);
                    Ll.property("ADBE Transform Group").property("ADBE Position").setValue([IW/2, lineY]);
                    var t0l=1e9; for(var z=0;z<tms.length;z++) if(tms[z]<t0l) t0l=tms[z]; if(t0l>=1e9)t0l=0; if(t0l<0)t0l=0;
                    var opL=Ll.property("ADBE Transform Group").property("ADBE Opacity");
                    opL.setValueAtTime(t0l,0); opL.setValueAtTime(t0l+F_DUR,100); easePair(opL);
                    continue;
                }
                // пословно: ширина всей строки и каждого слова -> раскладка как единый абзац (интервалы точные)
                var tmp=ic.layers.addText(""); introDoc(tmp, wds.join(" "), ln.color); var lineW=introW(tmp); tmp.remove();
                if(lineW>maxLineW) maxLineW=lineW;
                var wl=[], ww=[], sumW=0;
                for (var wj=0; wj<wds.length; wj++){
                    var L2=ic.layers.addText(""); introDoc(L2, wds[wj], ln.color);
                    var wpx=introW(L2); wl.push(L2); ww.push(wpx); sumW+=wpx;
                }
                var SPACE=(wds.length>1)?((lineW-sumW)/(wds.length-1)):0;
                var x=IW/2 - lineW/2;
                for (var wj2=0; wj2<wds.length; wj2++){
                    wl[wj2].property("ADBE Transform Group").property("ADBE Position").setValue([x+ww[wj2]/2, lineY]);
                    var op=wl[wj2].property("ADBE Transform Group").property("ADBE Opacity");
                    var tw=(tms[wj2]!=null?tms[wj2]:0); if(tw<0)tw=0;
                    op.setValueAtTime(tw, 0); op.setValueAtTime(tw+F_DUR, 100); easePair(op);
                    x += ww[wj2] + SPACE;
                }
            }
            var iL=main.layers.add(ic); iL.name=ic.name;
            introLayers.push(iL);
            var last=(gI==INTRO_GROUPS.length-1);
            var inAt=(gI==0&&gMin<3)?0:gMin;                // 1-я группа видна с 0 ТОЛЬКО если она реально в начале; серединные — по 1-му своему слову
            // outStart не раньше конца фейд-ина: у группы из ОДНОГО слова gMax==inAt,
            // и ключ «100» на outStart затирал ключ «0» на inAt — акцент влетал
            // мгновенно вместо кросс-фейда (а при gMax чуть меньше inAt+F_DUR
            // выход начинался раньше входа).
            var outStart=last?(gMax+F_DUR+HOLD):Math.max(gMax, inAt+F_DUR);
            var outEnd=outStart+F_OUT;
            // слой живёт с момента появления СВОИХ слов (серединный акцент не тянется с начала компа)
            iL.inPoint=(gI==0&&inAt==0)?0:inAt; iL.outPoint=outEnd;
            // родитель — общий нул «интро» (привязан к Null Камеры 1): все интро-прекомпы едут за кам1.
            // Группа, появляющаяся на перебивке, висит на своём нуле «интро на кам2» — чтобы её
            // (и все такие же) можно было опустить, не трогая интро на Камере 1
            var iPar = (INTRO_ON2[gI] ? introNull2 : introNull);
            // Масштаб слоя прекомпа = INTRO_SCALE (96.8%) × ds. ds несёт и ручной масштаб
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
            try{ var igl=iL.property("ADBE Effect Parade").addProperty("ADBE Glo2");
                 try{ igl.property("Glow Radius").setValue(42); }catch(e){}
                 try{ igl.property("Glow Intensity").setValue(INTRO_GLOW); }catch(e){} }catch(e){}
            dropShadow(iL, 68);
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
            cc.parent=nulls[ci];                    // сначала parent, ПОТОМ scale (иначе AE делит на зум Null)
            // ровно тот же масштаб, что у кадра своей камеры: рото-копия обязана лежать
            // пиксель-в-пиксель, иначе человек разъезжается с собственным кадром
            var rfit=100; try{ rfit = 100*Math.max(W/camSrc.width, H/camSrc.height); }catch(e){}
            var rsc = (ci==0) ? rfit*CAM1_FIT/100 : rr.scale;
            try{ cc.property("ADBE Transform Group").property("ADBE Scale").setValue([rsc,rsc]); }catch(e){}
            if (EXPOSURE!=0){ try{ var lc=cc.property("ADBE Effect Parade").addProperty("ADBE Lumetri");
                lc.property("ADBE Lumetri-0011").setValue(EXPOSURE); }catch(e){} }
            var mk = main.layers.add(maskIt); mk.name="Рото маска";   // альфа над копией
            mk.startTime=rr.ts; mk.inPoint=rr.ts; mk.outPoint=rr.te;
            mk.parent=nulls[ci];
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
    var ds = subLayer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");
    ds.property("ADBE Drop Shadow-0002").setValue(SH_OPACITY/100*255);  // Opacity (percent in UI -> 0..255)
    ds.property("ADBE Drop Shadow-0003").setValue(SH_DIR);      // Direction
    ds.property("ADBE Drop Shadow-0004").setValue(SH_DIST);     // Distance
    ds.property("ADBE Drop Shadow-0005").setValue(SH_SOFT);     // Softness
    if (SUB_HIDE.length){
        applyKeyframes(subLayer.property("ADBE Transform Group").property("ADBE Opacity"), SUB_HIDE);
    }

    // ---- раскладка слоёв по порядку из стиля (задание FM) ----
    var LAYER_ORDER = ["subs","video","roto","photo","intro"];
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
    for (var qn=0; qn<nullOrder.length; qn++){ var qN=nullOrder[qn]; if(!qN) continue;
        try{ qN.moveAfter(nullAnchor); nullAnchor=qN; }catch(e){} }

    try{ if (DISCLAIMER && dl) dl.moveToBeginning(); }catch(e){}   // дисклеймер поверх всего

    // Пробная композиция шрифта (задание ZF) своё отработала — в проекте ей делать нечего.
    // Удаляем ДО endUndoGroup: иначе «Отменить» вернёт её в панель проекта.
    if (_fontProbe){ try{ _fontProbe.remove(); }catch(e){ _LOG("пробная композиция шрифта: " + e); } }
    main.openInViewer();
    app.endUndoGroup();
})();
