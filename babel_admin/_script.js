
/* ============================================================
   1. 数据契约 —— 后端注入点
   TODO(后端对接): 用接口返回的真实数据整体替换本常量。
   字段名 / 层级不得改动：schema / asr / mt / modes / devices
   ============================================================ */
const BABEL_MODELS = {
  schema: 1,
  asr: [ {id:"local:medium", name:"faster-whisper medium", vendor:"faster-whisper", source:"local",
          langs:["en","ja","ko","vi","*"], vram_mb:1615, latency:"0.60s / 4s段",
          accuracy:"good", available:true, note:"默认·GPU 上由 small 自动升级"},
         {id:"remote:qwen3-asr-flash", name:"qwen3-asr-flash", vendor:"阿里云百炼", source:"remote",
          langs:["*"], vram_mb:0, latency:"0.90s / 4s段", accuracy:"ok", available:true, note:"无需 GPU，约 0.79 元/小时"} ],
  mt:  [ {id:"remote:qwen-mt-flash", name:"qwen-mt-flash", vendor:"阿里云百炼", source:"remote",
          langs:["*"], vram_mb:0, latency:"0.36s/段", quality:"best", available:true, note:"默认"},
         {id:"local:hh-translate-base", name:"HY-MT 1.8B (GGUF)", vendor:"llama.cpp", source:"local",
          langs:["*"], vram_mb:1100, latency:"0.49s/段", quality:"good", available:false, note:"llama-server.exe 未找到"},
         {id:"offline:argos-en-zh", name:"argos en→zh (1.9)", vendor:"argostranslate", source:"offline",
          langs:["en→zh"], vram_mb:0, latency:"0.40s/段", quality:"fair", available:true, note:"离线；无 ja→zh 包"} ],
  modes: {
    loopback: { label:"实时回环系统", asr:"local:medium", mt:"remote:qwen-mt-flash", preset:"低延迟" },
    cache:    { label:"视频缓存",     asr:"local:large-v3", mt:"remote:qwen-mt-flash", preset:"高准确" }
  },
  devices: [ {id:"cuda:0", name:"NVIDIA GeForce RTX 4070 Laptop GPU", total_mb:8188, role:"recognition"},
             {id:"cuda:0", name:"NVIDIA GeForce RTX 4070 Laptop GPU", total_mb:8188, role:"translation"} ]
};

/* ---------- 原型本地扩展（不属于数据契约，单独放） ---------- */
const BABEL_UI = {
  /* modes.asr 里出现的 id 若不在 asr 列表中（例如 local:large-v3），
     后端上线前用这张兜底表补一个不可用条目，避免界面空白 */
  asrPlaceholders: [
    { id:"local:large-v3", name:"faster-whisper large-v3", vendor:"faster-whisper", source:"local",
      langs:["en","ja","ko","vi","*"], vram_mb:3110, latency:"1.10s / 4s段",
      accuracy:"best", available:false, note:"未在本机 models 目录找到权重（约 3.1 GB）· 缓存模式需要它" }
  ],
  devicesExtra: [ { id:"cuda:0", name:"NVIDIA GeForce RTX 4070 Laptop GPU", total_mb:8188, role:"recognition" },
                  { id:"cuda:0", name:"NVIDIA GeForce RTX 4070 Laptop GPU", total_mb:8188, role:"translation" } ],
  /* 模式预设：实时=快，缓存=准 */
  presets: {
    loopback: { toolbar:{ input:"desktop", lang:"en→zh", fontSize:"20px", chunk:"2s", vprofile:"跟随系统音量" },
                asr:{ id:"local:medium", beam:"1（贪心）", vad:"DSP 门限 −38 dBFS" },
                mt:{ id:"remote:qwen-mt-flash", batch:2, temperature:0.10, top_p:0.60, top_k:20, repeat_penalty:1.05 },
                glossary:["巴别塔计划","浮窗","同传"],
                summary:"低延迟 · 识别 beam=1 · 翻译批次 2 段 · temperature 0.10" },
    cache:    { toolbar:{ input:"file", lang:"en→zh", fontSize:"22px", chunk:"16s", vprofile:"文件声道（原始）" },
                asr:{ id:"local:large-v3", beam:"5（beam search）", vad:"silero-vad 静音切分" },
                mt:{ id:"remote:qwen-mt-flash", batch:6, temperature:0.10, top_p:0.60, top_k:20, repeat_penalty:1.05 },
                glossary:["巴别塔计划","浮窗","同传","时间轴","停顿时长"],
                summary:"高准确 · 识别 beam=5 · 翻译批次 6 段 · 上下文重叠 1 段" }
  },
  asrParams: {
    loopback:{ model:"local:medium", beam:"1（贪心）", vad:"DSP 门限 −38 dBFS", chunk:"4s 段 · 1.5s 块" },
    cache:{ model:"local:large-v3", beam:"5（beam search）", vad:"silero-vad 静音切分", chunk:"16s 段 · 批量队列" }
  },
  preview:{ fontSize:20, lineHeight:1.45, lines:2, layout:"stack" },
  tasks:[
    {time:"14:02:11", text:"asr ready · local:medium", tone:"ok"},
    {time:"14:02:38", text:"mt ready · qwen-mt-flash", tone:"ok"},
    {time:"14:03:02", text:"overlay attached · topmost", tone:"ok"},
    {time:"14:05:47", text:"cache job #A231 完成 41 段", tone:"ok"},
    {time:"14:06:10", text:"HY-MT 1.8B 启动失败 · llama-server.exe 未找到", tone:"warn"}
  ],
  records:[
    {time:"14:02:11", src:"loopback", dir:"en → zh", dur:"18:24", model:"medium + qwen-mt-flash", state:"run"},
    {time:"13:41:02", src:"cache",    dir:"ja → zh", dur:"02:13:07", model:"large-v3 + qwen-mt-flash", state:"done"},
    {time:"12:58:44", src:"loopback", dir:"vi → zh", dur:"45:10", model:"medium + qwen-mt-flash", state:"done"},
    {time:"11:30:19", src:"cache",    dir:"en → zh", dur:"01:02:33", model:"large-v3 + argos en→zh", state:"done"},
    {time:"10:12:07", src:"loopback", dir:"ko → zh", dur:"07:52", model:"medium + qwen-mt-flash", state:"stop"},
    {time:"09:47:53", src:"cache",    dir:"en → zh", dur:"00:31:08", model:"large-v3 + qwen-mt-flash", state:"done"},
    {time:"09:20:31", src:"loopback", dir:"ja → zh", dur:"—", model:"medium + argos en→zh", state:"fail"},
    {time:"08:55:12", src:"cache",    dir:"vi → zh", dur:"00:18:45", model:"large-v3 + qwen-mt-flash", state:"done"},
    {time:"08:31:40", src:"loopback", dir:"en → zh", dur:"12:03", model:"medium + qwen-mt-flash", state:"done"},
    {time:"08:02:15", src:"cache",    dir:"ko → zh", dur:"00:44:20", model:"large-v3 + qwen-mt-flash", state:"stop"},
    {time:"07:48:02", src:"loopback", dir:"en → zh", dur:"26:11", model:"medium + qwen-mt-flash", state:"done"},
    {time:"07:20:55", src:"cache",    dir:"ja → zh", dur:"01:15:02", model:"large-v3 + qwen-mt-flash", state:"done"}
  ]
};

/* ============================================================
   1.5 模型数据加载链（后端对接点）
   顺序：fetch('/api/models') → fetch('model_registry_web.json') → 内置 BABEL_MODELS
   当前数据来源在侧边栏底部与工具栏角落显示：数据源 API / 快照 / 内置
   ============================================================ */
const MODELS = {                        // 运行时数据（API / 快照会整体替换；初始 = 内置假数据）
  schema: BABEL_MODELS.schema,
  asr: BABEL_MODELS.asr.slice(),
  mt: BABEL_MODELS.mt.slice(),
  modes: JSON.parse(JSON.stringify(BABEL_MODELS.modes)),
  devices: BABEL_MODELS.devices.slice()
};
const DATA_SOURCE = { kind:"builtin", detail:"内置假数据 BABEL_MODELS", errors:[], at:"" };

function normalizeModels(raw){
  if (!raw || typeof raw !== "object") return null;
  const asr = Array.isArray(raw.asr) ? raw.asr : (raw.asr && Array.isArray(raw.asr.models) ? raw.asr.models : null);
  const mt  = Array.isArray(raw.mt)  ? raw.mt  : (raw.mt  && Array.isArray(raw.mt.models)  ? raw.mt.models  : null);
  if (!asr || !mt) return null;
  const pick = function(list, fallback){
    return list.map(function(m, i){
      const f = fallback[i] || {};
      // available 三态：true 可用 / false 明确不可用 / null(缺字段) 未探测
      const known = (m.available === true || m.available === false);
      const avail = m.available === false ? false : true;
      const reason = m.unavailable_reason != null && m.unavailable_reason !== "" ? m.unavailable_reason
                   : m.unavailableReason != null && m.unavailableReason !== "" ? m.unavailableReason
                   : m.reason != null && m.reason !== "" ? m.reason : null;
      return {
        id:m.id != null ? m.id : f.id,
        name:m.name != null ? m.name : f.name,
        vendor:m.vendor != null ? m.vendor : f.vendor,
        source:m.source != null ? m.source : f.source,
        langs:Array.isArray(m.langs) ? m.langs : (f.langs || ["*"]),
        vram_mb:Number(m.vram_mb != null ? m.vram_mb : (f.vram_mb || 0)),
        latency:m.latency != null ? m.latency : f.latency,
        accuracy:m.accuracy != null ? m.accuracy : f.accuracy,
        quality:m.quality != null ? m.quality : f.quality,
        available:avail,
        probing:!known,                       // 后端没给可用性 → 不当作不可用，标注"未探测"
        wired:m.wired === true,               // 后端附加字段：是否接线到模块
        used_by:Array.isArray(m.used_by) ? m.used_by : [],   // 后端附加字段：归属哪些模块
        note:m.note != null ? m.note
           : reason != null ? reason
           : (f.note || (avail ? "" : "后端报告不可用（未给原因）"))
      };
    }).filter(function(m){ return !!m.id; });
  };
  return {
    schema: raw.schema != null ? raw.schema : 1,
    asr: pick(asr, MODELS.asr),
    mt: pick(mt, MODELS.mt),
    modes: (raw.modes && raw.modes.loopback && raw.modes.cache) ? raw.modes : MODELS.modes,
    devices: (Array.isArray(raw.devices) && raw.devices.length) ? raw.devices : MODELS.devices
  };
}
function adoptModels(raw, kind, detail){
  const norm = normalizeModels(raw);
  if (!norm){
    DATA_SOURCE.errors.push(kind + "：结构不符合契约（缺 asr / mt 数组）");
    return false;
  }
  MODELS.schema = norm.schema;
  MODELS.asr = norm.asr;
  MODELS.mt = norm.mt;
  MODELS.modes = norm.modes;
  MODELS.devices = norm.devices;
  DATA_SOURCE.kind = kind;
  DATA_SOURCE.detail = detail;
  DATA_SOURCE.at = new Date().toLocaleTimeString("zh-CN", { hour12:false });
  return true;
}
function loadModels(){
  const out = { kind:"builtin", detail:DATA_SOURCE.detail, errors:DATA_SOURCE.errors };
  const fetchJSON = function(url){
    return fetch(url, { headers:{ "Accept":"application/json" }, cache:"no-store" })
      .then(function(r){ if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
  };
  return fetchJSON("/api/models")
    .then(function(j){
      if (!adoptModels(j, "api", "GET /api/models")) throw new Error("响应字段与契约不匹配");
      out.kind = "api"; out.detail = DATA_SOURCE.detail;
    })
    .catch(function(e){
      out.errors.push("API 不可用：" + e.message + "（file:// 直接打开时属正常）");
      return fetchJSON("model_registry_web.json").then(function(j){
        if (!adoptModels(j, "snapshot", "model_registry_web.json（静态快照）")) throw new Error("响应字段与契约不匹配");
        out.kind = "snapshot"; out.detail = DATA_SOURCE.detail;
      });
    })
    .catch(function(e){
      out.errors.push("快照不可用：" + e.message);
      DATA_SOURCE.kind = "builtin";
      DATA_SOURCE.detail = "内置假数据（BABEL_MODELS）";
      out.kind = "builtin";
      return null;
    })
    .then(function(){ return out; });
}
function renderDataSource(){
  const map = {
    api:      { text:"API",  title:"GET /api/models（同源本地服务）" },
    snapshot: { text:"快照", title:"model_registry_web.json（同目录静态快照）" },
    builtin:  { text:"内置", title:"内置假数据 BABEL_MODELS" }
  };
  const info = map[DATA_SOURCE.kind] || map.builtin;
  ["#dsText", "#engineSrcTxt"].forEach(function(sel){
    const el = $(sel);
    if (el) el.textContent = info.text;
  });
  const dsText = $("#dsText");
  if (dsText){ dsText.parentNode.title = "数据源 " + info.text + " · " + info.title + " · " + (DATA_SOURCE.at || "内置，未联网加载"); }
  const tip = $("#engineSrc");
  if (tip) tip.title = info.title + " · " + DATA_SOURCE.detail;
  const note = $("#modelSrcNoteTxt");
  if (note){
    note.textContent = "模型数据来源：" + info.text + "（" + DATA_SOURCE.detail + "）· ASR 候选 " +
      modelList("asr").length + " 个 / MT 候选 " + modelList("mt").length + " 个" +
      (DATA_SOURCE.errors.length ? " · 降级记录：" + DATA_SOURCE.errors.join("；") : "");
  }
  const dot = $("#dsDot");
  if (dot){
    dot.style.background = DATA_SOURCE.kind === "api" ? "" :
                           DATA_SOURCE.kind === "snapshot" ? "#3B82F6" : "#94A3B8";
  }
}

/* ============================================================
   2. 工具
   ============================================================ */
const $  = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));
const esc = s => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const fmtMB = n => Number(n || 0).toLocaleString("en-US");
const clamp = (n, a, b) => Math.min(b, Math.max(a, n));

const ICONS = {
  wave:'<path d="M3 12h2.5l2-5.5L10.5 18 13 8.5 15 14.5 17 12h4"/>',
  film:'<rect x="3.2" y="4.4" width="17.6" height="15.2" rx="2.6"/><path d="M8.2 4.4v15.2M15.8 4.4v15.2M3.2 12h17.6"/>',
  rows:'<rect x="3.2" y="4.4" width="17.6" height="15.2" rx="2.6"/><path d="M3.2 9.6h17.6M3.2 14.6h17.6M9 9.6v10"/>',
  cube:'<path d="m12 3.2 7.4 3.9v9.8L12 20.8 4.6 16.9V7.1z"/><path d="m4.6 7.1 7.4 3.9 7.4-3.9M12 11v9.8"/>',
  gear:'<circle cx="12" cy="12" r="3"/><path d="M12 2.8v3M12 18.2v3M4.6 7.4l2.6 1.5M16.8 15.1l2.6 1.5M4.6 16.6l2.6-1.5M16.8 8.9l2.6-1.5"/>',
  chevron:'<path d="m6 9.5 6 6 6-6"/>',
  check:'<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  search:'<circle cx="11" cy="11" r="6.2"/><path d="m20 20-3.6-3.6"/>',
  mic:'<rect x="9.2" y="3" width="5.6" height="10.4" rx="2.8"/><path d="M5.6 11.6a6.4 6.4 0 0 0 12.8 0M12 18v3M8.6 21h6.8"/>',
  globe:'<circle cx="12" cy="12" r="8.6"/><path d="M3.4 12h17.2M12 3.4c2.6 2.4 3.9 5.4 3.9 8.6s-1.3 6.2-3.9 8.6c-2.6-2.4-3.9-5.4-3.9-8.6S9.4 5.8 12 3.4z"/>',
  gpu:'<rect x="3.2" y="6.2" width="17.6" height="11.6" rx="2.4"/><rect x="7.2" y="9.6" width="9.6" height="4.8" rx="1.2"/><path d="M7.4 6.2V3.6M12 6.2V3.6M16.6 6.2V3.6M7.4 17.8v2.6M12 17.8v2.6M16.6 17.8v2.6"/>',
  sliders:'<path d="M4.5 7.5h9M17 7.5h2.5M4.5 16.5h2.5M10.5 16.5h9"/><circle cx="15.2" cy="7.5" r="2.1"/><circle cx="8.8" cy="16.5" r="2.1"/>',
  activity:'<path d="M3.5 13h3.6l2.4-6 3.2 11 2.6-8 2 3h3.2"/>',
  captions:'<rect x="3.2" y="5.2" width="17.6" height="13.6" rx="2.6"/><path d="M7.4 10.4a2.4 2.4 0 1 0 0 3.2M15.4 10.4a2.4 2.4 0 1 0 0 3.2"/>',
  info:'<circle cx="12" cy="12" r="8.6"/><path d="M12 11v5.4M12 8.1v.1"/>',
  alert:'<path d="M12 4.6 20.2 19H3.8z"/><path d="M12 10v4M12 16.6v.1"/>',
  lock:'<rect x="4.8" y="10.6" width="14.4" height="9.4" rx="2.4"/><path d="M8.4 10.6V8a3.6 3.6 0 0 1 7.2 0v2.6"/>',
  bolt:'<path d="M13.2 3 6.4 13.4h5l-.6 7.6 6.8-10.4h-5z"/>',
  clock:'<circle cx="12" cy="12" r="8.6"/><path d="M12 7.6V12l3.2 1.9"/>',
  download:'<path d="M12 4.4v10.2M8.2 11l3.8 3.8 3.8-3.8M5 19.6h14"/>',
  play:'<circle cx="12" cy="12" r="8.6"/><path d="M10.4 9.2 15 12l-4.6 2.8z"/>',
  layers:'<path d="m12 3.6 8 4.2-8 4.2-8-4.2z"/><path d="m4 12 8 4.2 8-4.2M4 15.6 12 19.8l8-4.2"/>',
  file:'<path d="M6.4 3.4h7l4.6 4.6v12.6H6.4z"/><path d="M13.4 3.4v4.8h4.6"/>',
  desktop:'<rect x="3.2" y="4.6" width="17.6" height="12.4" rx="2.4"/><path d="M9 20.4h6M12 17v3.4"/>',
  monitor:'<rect x="3.2" y="4.6" width="17.6" height="12.4" rx="2.4"/><path d="M8 20.4h8"/>',
  speaker:'<path d="M4.6 9.4h3.2L12 5.6v12.8L7.8 14.6H4.6z"/><path d="M15.4 9.8a3.4 3.4 0 0 1 0 4.4"/>',
  font:'<path d="M4.6 19.4 11 4.6l6.4 14.8M7.2 13.6h7.6"/>',
  tag:'<path d="M4.6 11.6 11 5.2h7.2v7.2L11.8 19z"/><circle cx="15" cy="9" r="1.1"/>'
};
function icon(name, size, cls){
  const body = ICONS[name] || ICONS.info;
  return '<svg viewBox="0 0 24 24" width="' + (size || 18) + '" height="' + (size || 18) + '" fill="none" ' +
         'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"' +
         (cls ? ' class="' + cls + '"' : '') + ' aria-hidden="true">' + body + '</svg>';
}
/* Toast */
function toast(msg, sub, kind){
  const wrap = $("#toastWrap");
  while (wrap.children.length >= 3) wrap.removeChild(wrap.firstElementChild);
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  const icoName = kind === "warn" ? "alert" : kind === "info" ? "info" : "check";
  el.innerHTML = '<span class="t-ico">' + icon(icoName, 16) + '</span>' +
                 '<span class="t-txt">' + esc(msg) + (sub ? '<small>' + esc(sub) + '</small>' : '') + '</span>';
  wrap.appendChild(el);
  const kill = function(){
    el.classList.add("out");
    setTimeout(function(){ if (el.parentNode) el.parentNode.removeChild(el); }, 230);
  };
  const timer = setTimeout(kill, 2000);
  el.addEventListener("click", function(){ clearTimeout(timer); kill(); });
}

/* ============================================================
   3. 状态
   ============================================================ */
const state = {
  view: "models",
  mode: "loopback",
  asrId: MODELS.modes.loopback.asr,
  mtId:  MODELS.modes.loopback.mt,
  toolbar: Object.assign({}, BABEL_UI.presets.loopback.toolbar),
  params: Object.assign({}, BABEL_UI.presets.loopback.mt),
  glossary: BABEL_UI.presets.loopback.glossary.slice(),
  preview: Object.assign({}, BABEL_UI.preview),
  filter: { q:"", src:"all", dir:"all", state:"all" }
};

/* ============================================================
   4. 模型数据访问（含兜底）
   ============================================================ */
function modelList(kind){
  const list = Array.isArray(MODELS[kind]) ? MODELS[kind] : [];
  const base = list.slice();
  // 内置兜底占位只在真正用内置假数据时才补，避免后端注册表里出现"幽灵候选"
  if (kind === "asr" && DATA_SOURCE.kind === "builtin"){
    BABEL_UI.asrPlaceholders.forEach(function(ph){
      if (!base.some(function(m){ return m.id === ph.id; })) base.push(ph);
    });
  }
  return base;
}
function findModel(kind, id){
  return modelList(kind).filter(function(m){ return m.id === id; })[0] || null;
}
function firstAvailable(kind){
  const list = modelList(kind);
  return list.filter(function(m){ return m.available !== false; })[0] || list[0] || null;
}
function ensureSelectable(){
  const a = findModel("asr", state.asrId);
  if (!a || a.available === false){
    const fb = firstAvailable("asr");
    if (fb){ state.asrId = fb.id; }
  }
  const m = findModel("mt", state.mtId);
  if (!m || m.available === false){
    const fb = firstAvailable("mt");
    if (fb){ state.mtId = fb.id; }
  }
}
/* 按当前模式把 asr / mt 绑到注册表里的实际条目。
   id 查不到时**不静默沿用内置默认**：改取该模式第一个可用模型，并把缺失记进 state.bindAnomaly。 */
function resolveModeBinding(mode){
  const cfg = MODELS.modes[mode] || {};
  const bindAnomaly = [];
  const resolve = function(kind, wantId){
    const want = findModel(kind, wantId);
    if (want && want.available !== false) return { id:want.id, raw:wantId, why:"" };
    if (want && want.available === false){
      const fb = firstAvailable(kind);
      bindAnomaly.push({ kind:kind, want:wantId, got:fb ? fb.id : "—", why:"不可用", note:want.note || "" });
      return { id:fb ? fb.id : want.id, raw:wantId, why:"不可用" };
    }
    const ph = (kind === "asr" ? BABEL_UI.asrPlaceholders : []).filter(function(p){ return p.id === wantId; })[0];
    const fb2 = firstAvailable(kind);
    const got = (ph && ph.available !== false) ? ph : (fb2 || null);
    bindAnomaly.push({ kind:kind, want:wantId || "（模式未给）", got:got ? got.id : "—",
                       why:wantId ? "注册表里没有该 id" : "模式未绑定",
                       note:(ph && ph.note) || "" });
    return { id:got ? got.id : wantId, raw:wantId, why:"绑定缺失" };
  };
  const a = resolve("asr", cfg.asr);
  const m = resolve("mt",  cfg.mt);
  state.asrId = a.id;
  state.mtId = m.id;
  state.bindAnomaly = bindAnomaly;
  ensureSelectable();
}
function deviceFor(role){
  const list = (MODELS.devices || []).concat(BABEL_UI.devicesExtra || []);
  return list.filter(function(d){ return d.role === role; })[0] || list[0] || { name:"—", total_mb:0 };
}
function vramUsed(){
  const a = findModel("asr", state.asrId), m = findModel("mt", state.mtId);
  return { asr:Number(a ? a.vram_mb : 0) || 0, mt:Number(m ? m.vram_mb : 0) || 0 };
}
function pairLabel(){
  const a = findModel("asr", state.asrId), m = findModel("mt", state.mtId);
  return (a ? a.name : "未选择") + " + " + (m ? m.name : "未选择");
}
const QUALITY_TEXT = { best:"最佳", good:"良好", ok:"可用", fair:"一般", poor:"偏低" };

/* ============================================================
   5. 自定义下拉（JS 渲染 + 键盘可用）
   ============================================================ */
const dropdowns = {};
function createDropdown(host, cfg){
  const dd = document.createElement("div");
  dd.className = "dropdown";
  const options = cfg.options || [];
  const value = cfg.value;
  const cur = options.filter(function(o){ return o.value === value; })[0] || options[0] || null;
  dd.innerHTML =
    '<button class="dd-trigger" type="button" id="' + cfg.id + '" aria-haspopup="listbox" aria-expanded="false">' +
      (cfg.icon ? '<span class="dd-prefix">' + icon(cfg.icon, 16) + '</span>' : '') +
      '<span class="dd-label">' + esc(cur ? cur.label : cfg.placeholder || "请选择") + '</span>' +
      '<span class="dd-caret">' + icon("chevron", 16) + '</span>' +
    '</button>' +
    '<div class="dd-menu" role="listbox" aria-labelledby="' + cfg.id + '" tabindex="-1"></div>';
  const menu = $(".dd-menu", dd);
  const trigger = $(".dd-trigger", dd);
  const api = {};
  api.el = dd;
  api.set = function(v){
    const t = options.filter(function(o){ return o.value === v; })[0];
    if (!t) return;
    $(".dd-label", dd).textContent = t.label;
    $$(".dd-opt", menu).forEach(function(b){
      b.setAttribute("aria-selected", String(b.dataset.value === v));
    });
  };
  api.open = function(){
    closeAllDropdowns(dd);
    dd.classList.add("open");
    trigger.setAttribute("aria-expanded", "true");
    const sel = $('.dd-opt[aria-selected="true"]:not(.unavailable)', menu) || $(".dd-opt:not(.unavailable)", menu);
    if (sel) sel.focus();
  };
  api.close = function(){
    dd.classList.remove("open");
    trigger.setAttribute("aria-expanded", "false");
  };
  api.toggle = function(){ dd.classList.contains("open") ? api.close() : api.open(); };
  trigger.addEventListener("click", function(ev){ ev.stopPropagation(); api.toggle(); });
  options.forEach(function(o){
    const b = document.createElement("button");
    b.type = "button";
    b.className = "dd-opt" + (o.available === false ? " unavailable" : "");
    b.setAttribute("role", "option");
    b.setAttribute("aria-selected", String(o.value === value));
    b.dataset.value = o.value;
    b.tabIndex = -1;
    if (o.available === false) b.setAttribute("aria-disabled", "true");
    b.innerHTML =
      '<span class="opt-check">' + icon("check", 15) + '</span>' +
      '<span class="dd-opt-txt">' + esc(o.label) + (o.sub ? '<small>' + esc(o.sub) + '</small>' : '') + '</span>' +
      (o.badge ? '<span class="opt-badge ' + esc(o.badgeKind || "") + '">' + esc(o.badge) + '</span>' : '');
    b.addEventListener("click", function(ev){
      ev.stopPropagation();
      if (o.available === false){
        toast("该项不可用：" + o.label, o.unavailableReason || "后端未报告可用性", "warn");
        api.close();                    // 选中项不变，收起菜单避免"点了没反应"的错觉
        return;
      }
      api.set(o.value);
      api.close();
      if (typeof cfg.onSelect === "function") cfg.onSelect(o.value, o);
    });
    menu.appendChild(b);
  });
  trigger.addEventListener("keydown", function(ev){
    if (ev.key === "ArrowDown" || ev.key === "Enter" || ev.key === " "){
      ev.preventDefault(); api.open();
    }
  });
  menu.addEventListener("keydown", function(ev){
    const items = $$(".dd-opt:not(.unavailable)", menu);
    const idx = items.indexOf(document.activeElement);
    if (ev.key === "Escape"){ ev.preventDefault(); api.close(); trigger.focus(); }
    else if (ev.key === "ArrowDown"){ ev.preventDefault(); (items[idx + 1] || items[0]).focus(); }
    else if (ev.key === "ArrowUp"){ ev.preventDefault(); (items[idx - 1] || items[items.length - 1]).focus(); }
    else if (ev.key === "Home"){ ev.preventDefault(); items[0] && items[0].focus(); }
    else if (ev.key === "End"){ ev.preventDefault(); items[items.length - 1] && items[items.length - 1].focus(); }
    else if (ev.key === "Tab"){ api.close(); }
  });
  host.innerHTML = "";
  host.appendChild(dd);
  dropdowns[cfg.id] = { api: api, set: api.set };
  return api;
}
function closeAllDropdowns(except){
  $$(".dropdown.open").forEach(function(d){
    if (d === except) return;
    d.classList.remove("open");
    const t = $(".dd-trigger", d);
    if (t) t.setAttribute("aria-expanded", "false");
  });
}
document.addEventListener("click", function(){ closeAllDropdowns(null); });
document.addEventListener("keydown", function(ev){
  if (ev.key === "Escape") closeAllDropdowns(null);
});

/* ============================================================
   6. 顶部状态徽标
   ============================================================ */
function renderChips(){
  const used = vramUsed();
  const total = Number(deviceFor("recognition").total_mb || 0);
  const sum = used.asr + used.mt;
  const pct = total ? Math.round(sum / total * 100) : 0;
  const cur = activeModel("mt");
  const anomaly = (state.bindAnomaly || []);
  const anomalyChip = anomaly.length
    ? '<span class="chip" style="color:#B45309;background:#FFF7E8;border-color:#F7E0B0" title="' +
        esc(anomaly.map(function(x){ return x.kind.toUpperCase() + " " + x.want + " → " + x.got + "（" + x.why + "）"; }).join("；")) +
      '">⚠ <b>' + esc(anomaly.map(function(x){ return x.kind.toUpperCase(); }).join("/")) + '</b> 绑定缺失</span>'
    : "";
  $("#headChips").innerHTML =
    '<span class="chip ok"><span class="dot" aria-hidden="true"></span>运行中</span>' +
    anomalyChip +
    '<span class="chip mono">GPU <b>' + (18 + (pct % 55)) + '%</b> · <b>' + fmtMB(sum) + '</b> / ' + fmtMB(total) + ' MB</span>' +
    '<span class="chip mono">端到端 <b id="chipE2E">0.96s</b></span>' +
    '<span class="chip mono">MT <b>' + esc(cur ? cur.id : "—") + '</b></span>';
  const chip = $("#chipVram");
  if (chip) chip.textContent = fmtMB(sum) + " / " + fmtMB(total) + " MB";
}
function activeModel(kind){
  return findModel(kind, kind === "asr" ? state.asrId : state.mtId);
}

/* ============================================================
   7. 工具栏（随模式联动）
   ============================================================ */
function toolbarConfig(){
  const p = BABEL_UI.presets[state.mode];
  const asrOpts = modelList("asr").map(function(m){
    return { value:m.id, label:m.name, sub:(m.vendor || "") + " · " + (m.latency || ""),
             badge:m.source === "local" ? "本地" : m.source === "remote" ? "远端" : "离线",
             badgeKind:m.source, available:m.available !== false,
             unavailableReason:m.note || "" };
  });
  const mtOpts = modelList("mt").map(function(m){
    return { value:m.id, label:m.name, sub:(m.vendor || "") + " · " + (m.latency || ""),
             badge:m.source === "local" ? "本地" : m.source === "remote" ? "远端" : "离线",
             badgeKind:m.source, available:m.available !== false,
             unavailableReason:m.note || "" };
  });
  const langOpts = [
    { value:"en→zh", label:"英文 → 中文" }, { value:"ja→zh", label:"日文 → 中文" },
    { value:"ko→zh", label:"韩文 → 中文" }, { value:"vi→zh", label:"越南语 → 中文" },
    { value:"auto→zh", label:"自动检测 → 中文" }
  ];
  const fontOpts = [ {value:"18px",label:"18 px"},{value:"20px",label:"20 px"},
                     {value:"22px",label:"22 px"},{value:"24px",label:"24 px"},{value:"28px",label:"28 px"} ];
  if (state.mode === "loopback"){
    return [
      { id:"tb-input", label:"输入源", icon:"desktop", note:"实时回环只认设备音频",
        value:state.toolbar.input,
        options:[ {value:"desktop",label:"系统回环（扬声器）",sub:"WASAPI loopback · 48 kHz"},
                  {value:"mic",label:"麦克风阵列",sub:"16 kHz mono"},
                  {value:"mix",label:"系统 + 麦克风混音",sub:"18% / 82%"} ] },
      { id:"tb-asr", label:"识别模型", icon:"mic", note:"模式预设：" + p.asr.beam, wide:true,
        value:state.asrId, options:asrOpts },
      { id:"tb-mt", label:"翻译模型", icon:"globe", note:"模式预设：" + p.mt.batch + " 段/批", wide:true,
        value:state.mtId, options:mtOpts },
      { id:"tb-lang", label:"语言方向", icon:"globe", note:state.toolbar.lang.replace("→", " → "),
        value:state.toolbar.lang, options:langOpts },
      { id:"tb-chunk", label:"识别块长", icon:"bolt", note:"越小越跟手",
        value:p.toolbar.chunk,
        options:[ {value:"1s",label:"1 秒"},{value:"2s",label:"2 秒"},{value:"4s",label:"4 秒"} ] },
      { id:"tb-font", label:"字体大小", icon:"font", note:"浮窗字号",
        value:state.toolbar.fontSize, options:fontOpts }
    ];
  }
  return [
    { id:"tb-input", label:"输入源", icon:"film", note:"缓存模式读文件/播放器",
      value:state.toolbar.input,
      options:[ {value:"file",label:"本地视频文件",sub:"mp4 / mkv / mov"},
                {value:"player",label:"播放器缓存目录",sub:"watch_player.py 输出"},
                {value:"desktop",label:"系统回环（同步录）",sub:"边放边转"} ] },
    { id:"tb-asr", label:"识别模型", icon:"mic", note:"模式预设：" + p.asr.beam, wide:true,
      value:state.asrId, options:asrOpts },
    { id:"tb-mt", label:"翻译模型", icon:"globe", note:"模式预设：" + p.mt.batch + " 段/批", wide:true,
      value:state.mtId, options:mtOpts },
    { id:"tb-lang", label:"语言方向", icon:"globe", note:"全片语言可自动探测",
      value:state.toolbar.lang, options:langOpts },
    { id:"tb-chunk", label:"分段粒度", icon:"layers", note:"越大上下文越完整",
      value:p.toolbar.chunk,
      options:[ {value:"8s",label:"8 秒"},{value:"16s",label:"16 秒"},{value:"32s",label:"32 秒"} ] },
    { id:"tb-font", label:"字体大小", icon:"font", note:"导出字幕同样生效",
      value:state.toolbar.fontSize, options:fontOpts }
  ];
}
function renderToolbar(){
  const grid = $("#toolbarGrid");
  grid.innerHTML = "";
  toolbarConfig().forEach(function(cfg){
    const f = document.createElement("div");
    f.className = "field" + (cfg.wide ? " grow" : "");
    f.innerHTML = '<span class="field-label"><span class="fl-ico">' + icon(cfg.icon, 15) + '</span>' + esc(cfg.label) + '</span>' +
                  '<span class="dd-host"></span>' +
                  '<span class="field-note">' + esc(cfg.note || "") + '</span>';
    grid.appendChild(f);
    createDropdown($(".dd-host", f), {
      id: cfg.id, options: cfg.options, value: cfg.value,
      onSelect: function(v, o){ onToolbarChange(cfg.id, v, o); }
    });
  });
  const hint = $("#modeHint");
  if (hint) hint.textContent = "预设 · " + modePresetText(state.mode) + " ｜ " + BABEL_UI.presets[state.mode].summary;
}
/* 工具栏选中处理：绑定在下拉自身的 onSelect 上，重渲染后自动跟随，不依赖外部菜单监听 */
function onToolbarChange(id, v, o){
  const label = o && o.label ? o.label : v;
  if (id === "tb-asr"){
    state.asrId = v;
    syncAfterModelChange("识别模型已切换为 " + label);
  } else if (id === "tb-mt"){
    state.mtId = v;
    syncAfterModelChange("翻译模型已切换为 " + label);
  } else if (id === "tb-lang"){
    state.toolbar.lang = v;
    const note = document.querySelector("#tb-lang") ? document.querySelector("#tb-lang").parentNode.parentNode.querySelector(".field-note") : null;
    if (note) note.textContent = v.replace("→", " → ");
    toast("语言方向已设为 " + label);
  } else if (id === "tb-chunk"){
    state.toolbar.chunk = v;
    toast((state.mode === "loopback" ? "识别块长" : "分段粒度") + "已设为 " + label);
  } else if (id === "tb-font"){
    state.toolbar.fontSize = v;
    state.preview.fontSize = parseInt(v, 10);
    renderPreviewControls(); renderPreview();
    toast("浮窗字号已设为 " + label);
  } else if (id === "tb-input"){
    state.toolbar.input = v;
    toast("输入源已设为 " + label);
  }
}
function syncAfterModelChange(msg){
  renderModelCards(); renderDevices(); renderApplyText(); renderToolbar();
  renderStats(); renderChips(); renderAdvSummary();
  if (msg) toast(msg, pairLabel());
}

/* ============================================================
   8. 模型选择卡片
   ============================================================ */
function optCardHTML(kind, m, selected){
  const avail = m.available !== false;
  const langs = (m.langs || []).join(" / ");
  const rate = kind === "asr"
    ? '<span class="meta-pill">精度 <b class="ra-' + esc(m.accuracy || "ok") + '">' + esc(QUALITY_TEXT[m.accuracy] || m.accuracy || "—") + '</b></span>'
    : '<span class="meta-pill">质量 <b class="ra-' + esc(m.quality || "ok") + '">' + esc(QUALITY_TEXT[m.quality] || m.quality || "—") + '</b></span>';
  return '' +
  '<button class="opt-card' + (selected ? " selected" : "") + (avail ? "" : " unavailable") + '" type="button" ' +
     'role="radio" aria-checked="' + selected + '" data-kind="' + kind + '" data-id="' + esc(m.id) + '" ' +
     (avail ? "" : 'aria-disabled="true" ') + '>' +
    '<span class="oc-radio" aria-hidden="true">' + icon("check", 12) + '</span>' +
    '<span class="oc-name">' + esc(m.name) +
      '<span class="opt-badge ' + esc(m.source || "") + '">' +
        (m.source === "local" ? "本地" : m.source === "remote" ? "远端" : "离线") + '</span>' +
    '</span>' +
    '<div class="oc-vendor">' + esc(m.vendor || "") + ' · <span class="mono">' + esc(m.id) + '</span></div>' +
    '<div class="oc-meta">' +
      '<span class="meta-pill">显存 ' + (m.vram_mb ? fmtMB(m.vram_mb) + " MB" : "0（不占卡）") + '</span>' +
      '<span class="meta-pill">' + esc(m.latency || "—") + '</span>' +
      rate +
      '<span class="meta-pill">语种 ' + esc(langs || "*") + '</span>' +
      (avail ? (m.probing ? '<span class="meta-pill lock">' + icon("info", 11) + '未探测</span>' : '')
             : '<span class="meta-pill lock">' + icon("lock", 11) + '不可用</span>') +
      (m.wired ? '<span class="meta-pill" style="background:#E8F7EE;border-color:#CDEBD8;color:#0F5132">已接线</span>' : '') +
      (m.used_by && m.used_by.length ? '<span class="meta-pill">被 ' + esc(m.used_by.join(" / ")) + ' 使用</span>' : '') +
    '</div>' +
    '<div class="oc-note' + (avail ? "" : " bad") + '">' + icon(avail ? (m.probing ? "info" : "check") : "alert", 13) + '<span>' +
      esc(m.note || (m.probing ? "后端未报告可用性（未探测）" : "—")) +
      (avail ? (m.probing ? "（未探测，仍可选）" : "") : "（点击查看原因）") + '</span></div>' +
  '</button>';
}
function renderModelCards(){
  const asrSel = findModel("asr", state.asrId);
  const mtSel  = findModel("mt", state.mtId);
  const asrList = modelList("asr").slice().sort(function(a, b){ return (b.available === true) - (a.available === true); });
  const mtList  = modelList("mt").slice().sort(function(a, b){ return (b.available === true) - (a.available === true); });
  $("#asrGrid").innerHTML = asrList.map(function(m){ return optCardHTML("asr", m, asrSel && m.id === asrSel.id); }).join("");
  $("#mtGrid").innerHTML  = mtList.map(function(m){ return optCardHTML("mt",  m, mtSel && m.id === mtSel.id); }).join("");
  $("#asrCount").textContent = asrList.filter(function(m){ return m.available !== false; }).length + " / " + asrList.length + " 个可选";
  $("#mtCount").textContent  = mtList.filter(function(m){ return m.available !== false; }).length + " / " + mtList.length + " 个可选";
  const pair = $("#pairChip");
  if (pair) pair.textContent = (asrSel ? asrSel.name.split(" ")[0] : "—") + " + " + (mtSel ? mtSel.name.split(" ")[0] : "—");
  $$("#asrGrid .opt-card, #mtGrid .opt-card").forEach(function(btn){
    btn.addEventListener("click", function(){
      const kind = btn.dataset.kind, id = btn.dataset.id;
      const m = findModel(kind, id);
      if (!m) return;
      if (m.available === false){
        toast("不可用 · " + m.name, m.note || "后端未报告可用性", "warn");
        return;
      }
      if (kind === "asr"){ state.asrId = id; syncAfterModelChange("识别模型：" + m.name); }
      else { state.mtId = id; syncAfterModelChange("翻译模型：" + m.name); }
    });
  });
}
function renderDevices(){
  const used = vramUsed();
  const map = { recognition:{ used:used.asr, label:"识别", cls:"seg-a", mdl:activeModel("asr") },
                translation:{ used:used.mt,  label:"翻译", cls:"seg-b", mdl:activeModel("mt") } };
  const host = $("#deviceGrid");
  host.innerHTML = (MODELS.devices || []).map(function(d){
    const info = map[d.role] || { used:0, label:d.role, cls:"seg-a", mdl:null };
    const total = Number(d.total_mb || 0);
    const pct = total ? clamp(info.used / total * 100, 2, 100) : 0;
    return '' +
    '<div class="device-box">' +
      '<div class="device-top">' +
        '<span class="dv-role">' + esc(info.label) + '</span>' +
        '<span class="dv-arrow">' + icon("chevron", 14) + '</span>' +
        '<span>' + esc(d.name.replace("NVIDIA GeForce ", "")) + '</span>' +
        '<span class="dv-name">' + esc(d.id) + '</span>' +
      '</div>' +
      '<div class="bar" role="img" aria-label="' + esc(info.label) + '显存占用 ' + pct.toFixed(0) + '%">' +
        '<i class="' + info.cls + '" style="width:' + pct.toFixed(1) + '%"></i>' +
      '</div>' +
      '<div class="device-foot">' +
        '<span>' + fmtMB(info.used) + ' / ' + fmtMB(total) + ' MB</span>' +
        '<span>' + (total ? (info.used / total * 100).toFixed(1) : "0.0") + '% · ' +
          esc(info.mdl ? info.mdl.name : "未选择") + '</span>' +
      '</div>' +
    '</div>';
  }).join("");
  const total = Number(deviceFor("recognition").total_mb || 0);
  const sum = used.asr + used.mt;
  $("#deviceTag").textContent = "合计 " + fmtMB(sum) + " / " + fmtMB(total) + " MB";
  $("#deviceLegend").innerHTML =
    '<span><i style="background:linear-gradient(90deg,#16A34A,#22C55E)"></i>识别占用 ' + fmtMB(used.asr) + ' MB</span>' +
    '<span><i style="background:linear-gradient(90deg,#F59E0B,#FBBF24)"></i>翻译占用 ' + fmtMB(used.mt) + ' MB</span>' +
    '<span><i style="background:#EDF5F0;border:1px solid #E6EFE9"></i>空闲 ' + fmtMB(Math.max(0, total - sum)) + ' MB</span>';
  $("#vramHint").textContent = sum > total
    ? "⚠ 两套模型同时常驻会超出 " + fmtMB(total) + " MB，建议只保留一套本地模型。"
    : "剩余 " + fmtMB(total - sum) + " MB 可供浮窗渲染与视频解码缓存；" + (state.mode === "loopback" ? "实时模式建议保留 ≥ 1 GB 余量。" : "缓存模式可一次性吃满。");
}
function renderApplyText(){
  const a = activeModel("asr"), m = activeModel("mt");
  const modeLabel = (MODELS.modes[state.mode] && MODELS.modes[state.mode].label) || state.mode;
  const wanted = MODELS.modes[state.mode] || {};
  const warn = (state.bindAnomaly || []).map(function(x){
    return '<br><span style="color:#B45309">⚠ ' + esc(x.kind.toUpperCase()) +
           ' 模式绑定缺失：注册表里找不到 / 不可用 <code>' + esc(x.want) + '</code>' +
           '，已改用 <code>' + esc(x.got) + '</code>' + (x.note ? '（' + esc(x.note) + '）' : '') + '</span>';
  }).join("");
  $("#applyText").innerHTML =
    '将应用到 <b>实时回环系统</b> 与 <b>视频缓存</b> 两个模块：' +
    '<code>' + esc(a ? a.id : "—") + '</code> 负责识别，' +
    '<code>' + esc(m ? m.id : "—") + '</code> 负责翻译。' +
    '当前模式 <b>' + esc(modeLabel) + '</b> · 预设 <b>' + esc(modePresetText(state.mode)) + '</b>。' +
    '<br><span style="color:#94A3B8">模式绑定：' + esc(wanted.asr || "—") + ' + ' + esc(wanted.mt || "—") +
    ' · 合计显存 ' + fmtMB(vramUsed().asr + vramUsed().mt) + ' MB。</span>' + warn;}

/* ============================================================
   9. 高级参数
   ============================================================ */
function renderAdvanced(){
  const p = state.params;
  const host = $("#advBody");
  const sliders = [
    { key:"temperature", label:"temperature", min:0, max:1.5, step:0.05, unit:"", tip:"翻译要稳，别发挥" },
    { key:"top_p",       label:"top_p",       min:0.1, max:1, step:0.05, unit:"", tip:"核采样" },
    { key:"top_k",       label:"top_k",       min:1, max:100, step:1, unit:"", tip:"候选词上限" },
    { key:"repeat_penalty", label:"repeat_penalty", min:1, max:1.5, step:0.01, unit:"", tip:"官方推荐 1.05" }
  ];
  host.innerHTML = sliders.map(function(s){
    const pct = ((p[s.key] - s.min) / (s.max - s.min) * 100).toFixed(1);
    return '' +
    '<div class="slider-field">' +
      '<div class="slider-head"><span>' + esc(s.label) + ' <span style="color:#94A3B8">· ' + esc(s.tip) + '</span></span>' +
        '<b data-val="' + s.key + '">' + (s.key === "top_k" ? p[s.key] : Number(p[s.key]).toFixed(2)) + '</b></div>' +
      '<input type="range" data-slider="' + s.key + '" min="' + s.min + '" max="' + s.max + '" step="' + s.step + '" ' +
        'value="' + p[s.key] + '" style="--p:' + pct + '%" aria-label="' + esc(s.label) + '">' +
    '</div>';
  }).join("") +
  '<div>' +
    '<div class="slider-head"><span>合并批次 <span style="color:#94A3B8">· mt_cap，越大越省调用</span></span></div>' +
    '<span class="dd-host" id="advBatchHost"></span>' +
    '<div class="hint-line">实时预设 2 段/批，缓存预设 6 段/批</div>' +
  '</div>' +
  '<div>' +
    '<div class="slider-head"><span>术语表 <span style="color:#94A3B8">· 源词=译法</span></span></div>' +
    '<div class="tag-input" id="tagInput"></div>' +
    '<div class="hint-line">回车添加；仅本地 MT 生效</div>' +
  '</div>';

  createDropdown($("#advBatchHost"), {
    id:"adv-batch",
    options:[ {value:"1",label:"1 段（逐段，最跟手）"}, {value:"2",label:"2 段（实时预设）"},
              {value:"4",label:"4 段"}, {value:"6",label:"6 段（缓存预设）"}, {value:"12",label:"12 段（整段合并）"} ],
    value:String(p.batch || 2),
    onSelect: function(v, o){
      state.params.batch = Number(v);
      toast("合并批次已设为 " + (o.label || v), "本地 MT 生效");
      renderAdvSummary();
    }
  });
  $$('input[type="range"][data-slider]', host).forEach(function(r){
    r.addEventListener("input", function(){
      const key = r.dataset.slider;
      state.params[key] = Number(r.value);
      r.style.setProperty("--p", ((r.value - r.min) / (r.max - r.min) * 100).toFixed(1) + "%");
      const out = $('[data-val="' + key + '"]', host);
      if (out) out.textContent = (key === "top_k" ? r.value : Number(r.value).toFixed(2));
      renderAdvSummary();
    });
    r.addEventListener("change", function(){
      toast(r.dataset.slider + " = " + r.value, "翻译模块参数（原型不写盘）");
    });
  });
  renderTags();
  renderAdvSummary();
}
function renderTags(){
  const host = $("#tagInput");
  if (!host) return;
  host.innerHTML = state.glossary.map(function(t, i){
    return '<span class="tag">' + esc(t) + '<button type="button" data-tag-del="' + i + '" aria-label="移除 ' + esc(t) + '">' +
           icon("check", 10) + '</button></span>';
  }).join("") + '<input type="text" id="tagField" placeholder="' + (state.glossary.length ? "继续添加…" : "例如：巴别塔计划=The Babel Project") + '" aria-label="添加术语">';
  $$("[data-tag-del]", host).forEach(function(b){
    b.addEventListener("click", function(){
      const i = Number(b.dataset.tagDel);
      const gone = state.glossary.splice(i, 1)[0];
      renderTags(); renderAdvSummary();
      toast("已移除术语：" + gone, "仅本地 MT 生效");
    });
  });
  const input = $("#tagField");
  input.addEventListener("keydown", function(ev){
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    const v = input.value.trim();
    if (!v) return;
    state.glossary.push(v);
    renderTags(); renderAdvSummary();
    toast("已加入术语：" + v);
    const f = $("#tagField"); if (f) f.focus();
  });
}
function renderAdvSummary(){
  const p = state.params;
  $("#advSummary").textContent =
    "temperature " + Number(p.temperature).toFixed(2) +
    " · top_p " + Number(p.top_p).toFixed(2) +
    " · batch " + p.batch + " · 术语 " + state.glossary.length + " 条";
}

/* ============================================================
   10. 运行状态 / 趋势图
   ============================================================ */
const SERIES = {
  asr:[.58,.61,.57,.63,.60,.66,.59,.62,.64,.60,.58,.61,.63,.59,.60,.62,.61,.59,.60,.58],
  mt:[.34,.36,.35,.38,.36,.34,.37,.35,.36,.39,.36,.35,.37,.34,.36,.35,.38,.36,.35,.36],
  e2e:[.92,.97,.96,.99,.95,1.02,.94,.98,1.01,.96,.93,.97,1.00,.95,.96,.99,.98,.94,.96,.95]
};
function renderStats(){
  const used = vramUsed();
  const cards = [
    { label:"识别延迟", ico:"mic", cls:"",      num:SERIES.asr[SERIES.asr.length - 1].toFixed(2), unit:"s", sub:"local:medium · 4s 段 · 1.5s 块" },
    { label:"翻译延迟", ico:"globe", cls:"amber", num:SERIES.mt[SERIES.mt.length - 1].toFixed(2), unit:"s", sub:"qwen-mt-flash · 2 段/批" },
    { label:"端到端延迟", ico:"activity", cls:"blue", num:SERIES.e2e[SERIES.e2e.length - 1].toFixed(2), unit:"s", sub:"含浮窗渲染与置顶刷新" }
  ];
  $("#speedGrid").innerHTML = cards.map(function(c){
    return '<div class="speed-box ' + c.cls + '">' +
      '<div class="speed-label">' + icon(c.ico, 15) + esc(c.label) + '</div>' +
      '<div class="speed-num">' + c.num + '<small>' + c.unit + '</small></div>' +
      '<div class="speed-sub">' + esc(c.sub) + '</div>' +
    '</div>';
  }).join("");
  $("#modelStats").innerHTML =
    statBox("cube", "ASR 候选", String(modelList("asr").length), "个") +
    statBox("globe", "MT 候选", String(modelList("mt").length), "个") +
    statBox("gpu", "显存合计", fmtMB(used.asr + used.mt), "MB") +
    statBox("bolt", "当前预设", modePresetText(state.mode), "");
  $("#recStats").innerHTML =
    statBox("rows", "总记录", String(BABEL_UI.records.length), "条") +
    statBox("wave", "实时回环", String(BABEL_UI.records.filter(function(r){ return r.src === "loopback"; }).length), "条") +
    statBox("film", "视频缓存", String(BABEL_UI.records.filter(function(r){ return r.src === "cache"; }).length), "条") +
    statBox("clock", "总时长", "6:52:14", "");
  $("#sparkScale").textContent = "0s — 1.6s";
  renderSpark();
}
function statBox(ico, label, num, unit){
  return '<div class="stat-box">' +
    '<div class="st-label">' + icon(ico, 15) + esc(label) + '</div>' +
    '<div class="st-num">' + esc(num) + (unit ? '<small>' + esc(unit) + '</small>' : '') + '</div>' +
  '</div>';
}
function renderSpark(){
  const svg = $(".spark");
  if (!svg) return;
  const W = 320, H = 82, padX = 4, padY = 12;
  const all = SERIES.asr.concat(SERIES.mt, SERIES.e2e);
  const lo = Math.min.apply(null, all) * 0.86;
  const hi = Math.max.apply(null, all) * 1.04;
  const span = (hi - lo) || 1;
  const paths = [
    { data:SERIES.asr, color:"#12B76A" },
    { data:SERIES.mt,  color:"#F59E0B" },
    { data:SERIES.e2e, color:"#3B82F6" }
  ];
  const yOf = function(v){ return H - padY - ((v - lo) / span) * (H - padY * 2); };
  const grids = [0, 0.5, 1].map(function(g){
    const y = (padY + (H - padY * 2) * g).toFixed(1);
    return '<line x1="0" y1="' + y + '" x2="' + W + '" y2="' + y + '" stroke="#EDF4EF" stroke-width="1" stroke-dasharray="3 5"></line>';
  }).join("");
  const lines = paths.map(function(p){
    const pts = p.data.map(function(v, i){
      const x = padX + (i / (p.data.length - 1)) * (W - padX * 2);
      return x.toFixed(1) + "," + yOf(v).toFixed(1);
    }).join(" ");
    return '<polyline points="' + pts + '" fill="none" stroke="' + p.color + '" stroke-width="1.8" ' +
           'stroke-linecap="round" stroke-linejoin="round" opacity="0.95"></polyline>';
  }).join("");
  const lastY = yOf(SERIES.e2e[SERIES.e2e.length - 1]);
  svg.innerHTML = grids + lines +
    '<circle cx="' + W + '" cy="' + lastY.toFixed(1) + '" r="3" fill="#3B82F6" stroke="#fff" stroke-width="1.4"></circle>';
}

/* ============================================================
   11. 字幕预览
   ============================================================ */
function renderPreviewControls(){
  const host = $("#previewCtrls");
  const p = state.preview;
  const fontPct = ((p.fontSize - 14) / (34 - 14) * 100).toFixed(1);
  const lhPct = ((p.lineHeight - 1.1) / (2.2 - 1.1) * 100).toFixed(1);
  host.innerHTML = '' +
  '<div class="ctrl-row">' +
    '<div class="ctrl-top"><span>' + icon("font", 15) + ' 字号</span><b>' + p.fontSize + ' px</b></div>' +
    '<input type="range" data-pv="fontSize" min="14" max="34" step="1" value="' + p.fontSize + '" style="--p:' + fontPct + '%" aria-label="浮窗字号">' +
  '</div>' +
  '<div class="ctrl-row">' +
    '<div class="ctrl-top"><span>' + icon("sliders", 15) + ' 行距</span><b>' + Number(p.lineHeight).toFixed(2) + '</b></div>' +
    '<input type="range" data-pv="lineHeight" min="1.1" max="2.2" step="0.05" value="' + p.lineHeight + '" style="--p:' + lhPct + '%" aria-label="浮窗行距">' +
  '</div>' +
  '<div class="ctrl-row">' +
    '<div class="ctrl-top"><span>' + icon("captions", 15) + ' 条数</span><b>' + p.lines + ' 行</b></div>' +
    '<div class="pos-chips">' +
      [1,2,3].map(function(n){
        return '<button class="pos-chip' + (p.lines === n ? " active" : "") + '" type="button" data-pvlines="' + n + '">' + n + ' 行</button>';
      }).join("") +
    '</div>' +
  '</div>' +
  '<div class="ctrl-row">' +
    '<div class="ctrl-top"><span>' + icon("layers", 15) + ' 排布</span><b>' + (p.layout === "row" ? "同行" : p.layout === "stack" ? "上下" : "只译文") + '</b></div>' +
    '<div class="pos-chips">' +
      '<button class="pos-chip' + (p.layout === "row" ? " active" : "") + '" type="button" data-pvlayout="row">' + icon("rows", 13) + '同行</button>' +
      '<button class="pos-chip' + (p.layout === "stack" ? " active" : "") + '" type="button" data-pvlayout="stack">' + icon("layers", 13) + '上下</button>' +
      '<button class="pos-chip' + (p.layout === "zh" ? " active" : "") + '" type="button" data-pvlayout="zh">' + icon("check", 13) + '只译文</button>' +
    '</div>' +
  '</div>';
  $$('input[type="range"][data-pv]', host).forEach(function(r){
    r.addEventListener("input", function(){
      const k = r.dataset.pv;
      state.preview[k] = Number(r.value);
      r.style.setProperty("--p", ((r.value - r.min) / (r.max - r.min) * 100).toFixed(1) + "%");
      const b = $(".ctrl-top b", r.closest(".ctrl-row"));
      if (b) b.textContent = k === "fontSize" ? r.value + " px" : Number(r.value).toFixed(2);
      renderPreview();
    });
  });
  $$("[data-pvlines]", host).forEach(function(b){
    b.addEventListener("click", function(){
      state.preview.lines = Number(b.dataset.pvlines);
      renderPreviewControls(); renderPreview();
      toast("浮窗条数：" + state.preview.lines + " 行");
    });
  });
  $$("[data-pvlayout]", host).forEach(function(b){
    b.addEventListener("click", function(){
      state.preview.layout = b.dataset.pvlayout;
      renderPreviewControls(); renderPreview();
      toast("排布方式：" + (state.preview.layout === "row" ? "同行" : state.preview.layout === "stack" ? "上下" : "只译文"));
    });
  });
}
function renderPreview(){
  const p = state.preview;
  const zh = $("#previewZh"), src = $("#previewSrc"), box = $(".sub-box");
  zh.style.fontSize = p.fontSize + "px";
  zh.style.lineHeight = p.lineHeight;
  src.style.fontSize = Math.max(11, p.fontSize - 4) + "px";
  src.style.lineHeight = p.lineHeight;
  const stack = p.layout !== "row";
  box.style.display = p.layout === "zh" && p.lines === 1 ? "flex" : "flex";
  box.style.flexDirection = stack ? "column" : "row";
  box.style.alignItems = stack ? "center" : "baseline";
  box.style.gap = (stack ? 4 : 14) + "px";
  src.style.display = p.layout === "zh" ? "none" : "block";
  zh.textContent = p.lines >= 2
    ? "你正在看的这段视频，字幕会实时翻译成中文"
    : "字幕会实时翻译成中文";
  src.textContent = p.lines >= 3
    ? "Every line of this video is translated into Chinese as you watch it, without waiting for the whole file."
    : "Every line of this video is translated into Chinese as you watch.";
}

/* ============================================================
   12. 记录表
   ============================================================ */
const SRC_LABEL = { loopback:"实时回环", cache:"视频缓存" };
const STATE_LABEL = { done:"已完成", run:"运行中", stop:"已停止", fail:"失败" };
function statusHTML(s){ return '<span class="status ' + s + '">' + esc(STATE_LABEL[s] || s) + '</span>'; }
function recordsTable(idPrefix){
  return '' +
  '<div class="table-tools">' +
    '<label class="search">' + icon("search", 16) +
      '<input type="search" id="' + idPrefix + '-q" placeholder="搜索时间 / 语种 / 模型…" aria-label="搜索记录">' +
    '</label>' +
    '<div class="tools-right">' +
      '<span class="dd-host" id="' + idPrefix + '-src-host" style="min-width:150px;display:inline-block"></span>' +
      '<span class="dd-host" id="' + idPrefix + '-dir-host" style="min-width:150px;display:inline-block"></span>' +
      '<span class="dd-host" id="' + idPrefix + '-state-host" style="min-width:130px;display:inline-block"></span>' +
    '</div>' +
  '</div>' +
  '<div class="table-scroll">' +
    '<table class="rec"><thead><tr>' +
      '<th>时间</th><th>来源</th><th>语种方向</th><th>时长</th><th>模型</th><th>状态</th><th>操作</th>' +
    '</tr></thead><tbody id="' + idPrefix + '-body"></tbody></table>' +
  '</div>' +
  '<div class="table-foot">' +
    '<span id="' + idPrefix + '-count">—</span>' +
    '<span class="mono">数据来源：BABEL_UI.records（假数据）</span>' +
    '<button class="link-btn" type="button" data-toast="加载更多（原型占位）">加载更多</button>' +
  '</div>';
}
function mountRecords(host, id){
  host.innerHTML = recordsTable(id);
  const onFilter = function(key){
    return function(v){
      state.filter[key] = v;
      renderRecords();
    };
  };
  createDropdown($("#" + id + "-src-host"), { id:id + "-src-dd", value:"all", onSelect:onFilter("src"), options:[
    {value:"all",label:"全部来源"}, {value:"loopback",label:"实时回环"}, {value:"cache",label:"视频缓存"} ] });
  createDropdown($("#" + id + "-dir-host"), { id:id + "-dir-dd", value:"all", onSelect:onFilter("dir"), options:[
    {value:"all",label:"全部语种"}, {value:"en → zh",label:"en → zh"}, {value:"ja → zh",label:"ja → zh"},
    {value:"ko → zh",label:"ko → zh"}, {value:"vi → zh",label:"vi → zh"} ] });
  createDropdown($("#" + id + "-state-host"), { id:id + "-state-dd", value:"all", onSelect:onFilter("state"), options:[
    {value:"all",label:"全部状态"}, {value:"done",label:"已完成"}, {value:"run",label:"运行中"},
    {value:"stop",label:"已停止"}, {value:"fail",label:"失败"} ] });
  $("#" + id + "-q").addEventListener("input", function(ev){
    state.filter.q = ev.target.value.trim();
    renderRecords();
    const other = document.getElementById((id === "rec1" ? "rec2" : "rec1") + "-q");
    if (other) other.value = state.filter.q;   // 两张表共用一份筛选状态
  });
}
function renderRecords(){
  const f = state.filter;
  const rows = BABEL_UI.records.filter(function(r){
    if (f.src !== "all" && r.src !== f.src) return false;
    if (f.dir !== "all" && r.dir !== f.dir) return false;
    if (f.state !== "all" && r.state !== f.state) return false;
    if (f.q){
      const hay = (r.time + " " + SRC_LABEL[r.src] + " " + r.dir + " " + r.model + " " + STATE_LABEL[r.state]).toLowerCase();
      if (hay.indexOf(f.q.toLowerCase()) < 0) return false;
    }
    return true;
  });
  const bodyHTML = rows.length ? rows.map(function(r){
      return '<tr>' +
        '<td class="mono">' + esc(r.time) + '</td>' +
        '<td><span class="row-src">' + icon(r.src === "loopback" ? "wave" : "film", 14) + esc(SRC_LABEL[r.src] || r.src) + '</span></td>' +
        '<td class="mono">' + esc(r.dir) + '</td>' +
        '<td class="mono">' + esc(r.dur) + '</td>' +
        '<td class="mono muted">' + esc(r.model) + '</td>' +
        '<td>' + statusHTML(r.state) + '</td>' +
        '<td><button class="link-btn" type="button" data-toast="打开会话详情（原型占位）">查看</button></td>' +
      '</tr>';
    }).join("") : '<tr class="empty-row"><td colspan="7">没有匹配的记录，换个关键词试试。</td></tr>';
  ["rec1", "rec2"].forEach(function(id){
    const body = document.getElementById(id + "-body");
    if (body) body.innerHTML = bodyHTML;
    const count = document.getElementById(id + "-count");
    if (count) count.textContent = "共 " + rows.length + " 条（筛选后）/ " + BABEL_UI.records.length + " 条";
  });
}

/* ============================================================
   13. 日志条 / 设置页
   ============================================================ */
function renderLogStrip(){
  const host = $("#logStrip");
  if (!host) return;
  host.innerHTML = '<span class="ls-title">' + icon("activity", 15) + '最近任务</span>' +
    BABEL_UI.tasks.slice(0, 3).map(function(t){
      return '<span class="log-item"' + (t.tone === "warn" ? ' style="color:#B45309"' : '') + '>' +
             '<b>' + esc(t.time) + '</b> ' + esc(t.text) + '</span>';
    }).join("") +
    '<button class="link-btn" type="button" style="margin-left:auto" data-toast="run.log 全量日志（原型占位）">查看 run.log</button>';
}
function renderSettings(){
  const host = $("#settingsGrid");
  if (!host) return;
  host.innerHTML = '' +
  '<article class="card"><div class="card-head"><div><h2>' + icon("bolt", 16) + ' 浮窗行为</h2>' +
    '<p class="card-desc">影响置顶浮窗的显示与热键</p></div></div>' +
    setRow("浮窗置顶", "always on top · 不抢焦点", "switch", true) +
    setRow("只显示译文", "隐藏原文行", "switch", false) +
    setRow("热键：暂停翻译", "", "kbd", "Ctrl+Alt+P") +
    setRow("热键：切换浮窗", "", "kbd", "Ctrl+Alt+H") +
  '</article>' +
  '<article class="card"><div class="card-head"><div><h2>' + icon("globe", 16) + ' 与远端接口</h2>' +
    '<p class="card-desc">阿里云百炼 / OpenAI 兼容端点</p></div></div>' +
    setRow("接口地址", "https://dashscope.aliyuncs.com/compatible-mode/v1", "switch", true) +
    setRow("API Key", "sk-••••••••••••••••••••", "kbd", "已保存") +
    setRow("调用超时", "8.0 s", "kbd", "8.0s") +
    setRow("离线回退", "远端失败时用 argos", "switch", true) +
  '</article>' +
  '<article class="card"><div class="card-head"><div><h2>' + icon("gpu", 16) + ' 设备与显存</h2>' +
    '<p class="card-desc">device_pref.json 的可视化占位</p></div></div>' +
    setRow("首选设备", "cuda:0", "kbd", "cuda:0") +
    setRow("识别放置", "GPU", "switch", true) +
    setRow("翻译放置", "GPU（本地 MT）", "switch", true) +
    setRow("显存上限", "7,400 MB", "kbd", "7,400 MB") +
  '</article>' +
  '<article class="card"><div class="card-head"><div><h2>' + icon("file", 16) + ' 路径与输出</h2>' +
    '<p class="card-desc">字幕落盘位置（原型占位）</p></div></div>' +
    setRow("输出目录", "D:\\NEW WORK\\work\\live-subtitle", "kbd", "默认") +
    setRow("同时写 SRT", "live.srt 实时覆盖", "switch", true) +
    setRow("双语对照输出", "中文 + 原文", "switch", false) +
    setRow("开机自启", "start.bat", "switch", false) +
  '</article>';
  $$(".switch", host).forEach(function(sw){
    sw.setAttribute("role", "switch");
    sw.setAttribute("tabindex", "0");
    sw.setAttribute("aria-checked", sw.classList.contains("on") ? "true" : "false");
    const flip = function(){
      sw.classList.toggle("on");
      sw.setAttribute("aria-checked", sw.classList.contains("on") ? "true" : "false");
      toast("开关已" + (sw.classList.contains("on") ? "开启" : "关闭"), "原型不写盘");
    };
    sw.addEventListener("click", flip);
    sw.addEventListener("keydown", function(ev){ if (ev.key === "Enter" || ev.key === " "){ ev.preventDefault(); flip(); } });
  });
}
function setRow(label, sub, kind, val){
  let right = "";
  if (kind === "switch") right = '<span class="switch' + (val ? " on" : "") + '"></span>';
  else if (kind === "kbd") right = '<span class="kbd">' + esc(val) + '</span>';
  return '<div class="set-row"><span class="sr-label">' + esc(label) + (sub ? '<small>' + esc(sub) + '</small>' : '') + '</span>' + right + '</div>';
}

/* ============================================================
   14. 视图 / 模式切换
   ============================================================ */
const VIEW_TITLE = {
  loopback:{ h:"直播翻译和视频翻译", s:"模块：<b>实时回环系统</b> · 边放边译，输入来自系统回环" },
  cache:   { h:"直播翻译和视频翻译", s:"模块：<b>视频缓存</b> · 播放器缓存目录批量翻译，追求准确" },
  models:  { h:"模型管理",           s:"ASR / MT 候选表 · 显存预算 · 可用性与不可用原因 · 归属模块" },
  records: { h:"同传记录",           s:"全部会话记录 · 搜索与筛选即时生效" },
  settings:{ h:"设置",               s:"浮窗 / 接口 / 设备 / 路径（占位）" }
};
function setView(view){
  state.view = view;
  const navKey = (view === "loopback" || view === "cache") ? view : view;
  $$(".nav-item").forEach(function(b){
    const on = b.dataset.nav === navKey;
    b.classList.toggle("active", on);
    b.setAttribute("aria-current", on ? "page" : "false");
  });
  const cardView = (view === "loopback" || view === "cache") ? "translate" : view;
  $$(".view").forEach(function(v){ v.classList.toggle("active", v.dataset.view === cardView); });
  if (view === "loopback" || view === "cache"){
    if (state.mode !== view) applyMode(view, false);
    else renderToolbar();
  }
  const t = VIEW_TITLE[view] || VIEW_TITLE.loopback;
  $("#pageTitle").textContent = t.h;
  $("#pageSub").innerHTML = t.s;
  renderChips(); renderDataSource();
}
function applyMode(mode, announce){
  state.mode = mode;
  const preset = BABEL_UI.presets[mode];
  const cfg = MODELS.modes[mode];
  resolveModeBinding(mode);            // 绑定 + 缺失标注（不许静默回退）
  state.toolbar = Object.assign({}, preset.toolbar);
  state.params = Object.assign({}, preset.mt);
  state.glossary = preset.glossary.slice();
  state.preview.fontSize = parseInt(preset.toolbar.fontSize, 10);
  renderToolbar();
  renderModeSeg();
  renderModelCards(); renderDevices(); renderApplyText();
  renderAdvanced();
  renderPreviewControls(); renderPreview();
  renderStats(); renderChips(); renderLogStrip();
  if (announce){
    const bad = state.bindAnomaly.length
      ? "⚠ " + state.bindAnomaly.map(function(x){ return x.kind.toUpperCase() + " " + x.why; }).join(" / ")
      : "";
    toast("已切到「" + (cfg ? cfg.label : mode) + "」",
          "预设 " + modePresetText(mode) + " · " + pairLabel() + (bad ? " · " + bad : ""));
  }
}
/* 预设文案：后端没给 preset 时用 params 自己拼一句，绝不出现 undefined */
function modePresetText(mode){
  const cfg = (MODELS.modes && MODELS.modes[mode]) || {};
  if (cfg.preset) return String(cfg.preset);
  const p = cfg.params || cfg.asr_stream || {};
  const bits = [];
  const batch = p.mt_batch != null ? p.mt_batch : (p.batch != null ? p.batch : null);
  if (batch != null) bits.push("mt_batch " + batch + " 段/批");
  const chunk = p.asr_chunk != null ? p.asr_chunk : (p.chunk != null ? p.chunk : null);
  if (chunk != null) bits.push("识别块 " + chunk);
  else if (p.mt_cap != null) bits.push("mt_cap " + p.mt_cap);
  if (p.temperature != null) bits.push("temperature " + p.temperature);
  if (bits.length) return bits.join(" · ");
  const ui = BABEL_UI.presets[mode];
  return (ui && ui.summary) ? ui.summary : "后端未给预设";
}
function renderModeSeg(){
  const seg = $("#modeSeg");
  seg.innerHTML = ["loopback", "cache"].map(function(k){
    const on = state.mode === k;
    const label = (MODELS.modes[k] && MODELS.modes[k].label) || (k === "loopback" ? "实时回环系统" : "视频缓存");
    return '<button class="seg-item' + (on ? " active" : "") + '" type="button" data-mode="' + k + '" ' +
           'aria-pressed="' + on + '">' +
           '<span class="si-ico">' + icon(k === "loopback" ? "wave" : "film", 15) + '</span>' +
           esc(label) +
           '<span class="nav-tag">' + esc(modePresetText(k)) + '</span></button>';
  }).join("");
  $$("[data-mode]", seg).forEach(function(b){
    b.addEventListener("click", function(){ setView(b.dataset.mode); });
  });
}
function bindNav(){
  $$(".nav-item").forEach(function(b){
    b.addEventListener("click", function(){ setView(b.dataset.nav); });
  });
}
function bindGlobal(){
  document.addEventListener("click", function(ev){
    const t = ev.target.closest("[data-toast]");
    if (t) toast(t.dataset.toast, "", "info");
    const ap = ev.target.closest("[data-apply]");
    if (ap){
      const which = ap.dataset.apply;
      if (which === "both"){
        MODELS.modes.loopback.asr = state.asrId; MODELS.modes.loopback.mt = state.mtId;
        MODELS.modes.cache.asr = state.asrId;    MODELS.modes.cache.mt = state.mtId;
        toast("已应用到两个模块", pairLabel());
      } else {
        MODELS.modes[which].asr = state.asrId;
        MODELS.modes[which].mt = state.mtId;
        toast("已应用到「" + MODELS.modes[which].label + "」", pairLabel());
      }
      renderModeSeg(); renderApplyText();
    }
  });
  const badge = $("#engineBadge");
  let online = true;
  badge.addEventListener("click", function(){
    online = !online;
    badge.style.color = online ? "" : "#B45309";
    badge.style.background = online ? "" : "#FFF7E8";
    badge.style.borderColor = online ? "" : "#F7E0B0";
    $(".dot", badge).style.background = online ? "" : "#F59E0B";
    $("#engineTxt").textContent = online ? "引擎在线" : "引擎离线";
    toast(online ? "引擎在线" : "引擎离线（原型演示）", "点击状态点切换", online ? "" : "warn");
  });
}

/* ============================================================
   15. 启动
   ============================================================ */
function boot(){
  // 1) 先把两张同传记录表挂好（模型管理视图里那份从模板克隆）
  const slot = $("#recordsCardSlot");
  if (slot) slot.appendChild($("#recordsCardTpl").content.cloneNode(true));
  mountRecords($("#recordsHost"), "rec1");
  mountRecords($("#recordsHostFull"), "rec2");
  renderSettings();

  // 2) 交互与视图骨架
  renderModeSeg();
  bindNav();
  bindGlobal();
  applyMode("loopback", false);  // 先按内置数据把工作台整条渲染管线跑一遍
  setView("loopback");           // 默认落地：翻译字幕工作台 · 实时回环系统
  renderDataSource();

  // 3) 拉模型数据：API → 快照 → 内置；回来后按新注册表重新绑定模式并整页重渲染
  loadModels().then(function(info){
    renderDataSource();
    renderModeSeg();
    resolveModeBinding(state.mode);   // 关键：注册表换了，模式绑定必须重算（否则选中项落空）
    renderToolbar();
    renderModelCards(); renderDevices(); renderApplyText();
    renderStats(); renderChips(); renderAdvanced();
    renderPreviewControls(); renderPreview(); renderLogStrip();
    renderRecords();
    setView(state.view);
    if (info.kind === "builtin"){
      toast("模型数据用内置假数据", "API 与快照都不可用（file:// 直接打开时正常）", "warn");
    } else if (state.bindAnomaly && state.bindAnomaly.length){
      toast("模型数据已加载", "数据源：" + (info.kind === "api" ? "API /api/models" : "快照") +
            " · ⚠ " + state.bindAnomaly.map(function(x){ return x.kind.toUpperCase() + " " + x.why; }).join(" / "), "warn");
    } else {
      toast("模型数据已加载", "数据源：" + (info.kind === "api" ? "API /api/models" : "快照 model_registry_web.json"));
    }
  });
}
document.addEventListener("DOMContentLoaded", boot);
