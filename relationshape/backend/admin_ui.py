"""HTML shell for the backend admin console."""

ADMIN_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>relationshape 后台</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --panel2: #f9fafb;
      --ink: #18202f;
      --muted: #667085;
      --subtle: #98a2b3;
      --line: #dde3eb;
      --line2: #edf0f4;
      --accent: #246bfe;
      --accent2: #13a887;
      --warn: #ca8504;
      --bad: #d92d20;
      --radius: 8px;
      --shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 10px 30px rgba(16, 24, 40, .06);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      line-height: 1.45;
    }
    button, input, select, textarea { font: inherit; }
    button { cursor: pointer; }
    .shell { min-height: 100vh; display: grid; grid-template-columns: 248px minmax(0, 1fr); }
    .sidebar {
      background: #101828;
      color: #e4e7ec;
      padding: 20px 16px;
      position: sticky;
      top: 0;
      height: 100vh;
    }
    .brand { font-weight: 760; font-size: 18px; margin: 4px 8px 22px; letter-spacing: .2px; }
    .brand span { display: block; font-weight: 500; font-size: 12px; color: #98a2b3; margin-top: 4px; }
    .nav { display: grid; gap: 4px; }
    .nav button {
      width: 100%;
      display: flex;
      align-items: center;
      gap: 10px;
      border: 0;
      background: transparent;
      color: #cbd5e1;
      padding: 10px 12px;
      border-radius: 8px;
      text-align: left;
      font-size: 14px;
    }
    .nav button:hover { background: rgba(255,255,255,.07); color: white; }
    .nav button.active { background: #246bfe; color: white; }
    .nav svg { width: 17px; height: 17px; flex: none; }
    .side-foot {
      position: absolute;
      bottom: 18px;
      left: 16px;
      right: 16px;
      color: #98a2b3;
      font-size: 12px;
      border-top: 1px solid rgba(255,255,255,.1);
      padding-top: 14px;
    }
    .main { min-width: 0; }
    .topbar {
      height: 64px;
      background: rgba(255,255,255,.92);
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 0 28px;
      position: sticky;
      top: 0;
      z-index: 5;
      backdrop-filter: blur(10px);
    }
    .topbar h1 { font-size: 18px; margin: 0; }
    .spacer { flex: 1; }
    .select, input, textarea {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: white;
      color: var(--ink);
      padding: 9px 10px;
      min-height: 38px;
    }
    .btn {
      border: 1px solid var(--line);
      background: white;
      color: var(--ink);
      border-radius: 8px;
      padding: 9px 12px;
      font-size: 14px;
    }
    .btn.primary { background: var(--accent); color: white; border-color: var(--accent); }
    .btn.ghost { background: transparent; }
    .btn.danger { color: var(--bad); border-color: #f2b8b5; }
    .content { padding: 26px 28px 48px; }
    .section { display: none; }
    .section.active { display: block; }
    .section-head {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }
    .section-head h2 { margin: 0; font-size: 24px; letter-spacing: -.2px; }
    .section-head p { margin: 4px 0 0; color: var(--muted); font-size: 14px; }
    .grid { display: grid; gap: 14px; }
    .grid.kpis { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .grid.two { grid-template-columns: minmax(0, 1.05fr) minmax(0, .95fr); }
    .grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .panel, .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .panel { padding: 18px; }
    .card { padding: 16px; }
    .kpi .label { color: var(--muted); font-size: 13px; }
    .kpi .value { font-size: 28px; font-weight: 760; margin-top: 6px; letter-spacing: -.5px; }
    .kpi .hint { color: var(--subtle); font-size: 12px; margin-top: 6px; }
    .panel-title { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
    .panel-title h3 { margin: 0; font-size: 15px; }
    .panel-title span { color: var(--subtle); font-size: 12px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; color: var(--muted); font-weight: 650; border-bottom: 1px solid var(--line2); padding: 10px 8px; }
    td { border-bottom: 1px solid var(--line2); padding: 11px 8px; vertical-align: top; }
    tr:last-child td { border-bottom: 0; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .muted { color: var(--muted); }
    .tag {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 2px 8px;
      background: #eef4ff;
      color: #1849a9;
      font-size: 12px;
      font-weight: 650;
    }
    .tag.green { background: #ecfdf3; color: #027a48; }
    .tag.gray { background: #f2f4f7; color: #475467; }
    .tag.red { background: #fef3f2; color: #b42318; }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .form-grid label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; }
    .form-grid .wide { grid-column: 1 / -1; }
    textarea { min-height: 82px; resize: vertical; }
    .notice {
      border: 1px dashed #b2ccff;
      background: #eff6ff;
      color: #1849a9;
      padding: 12px 14px;
      border-radius: 8px;
      margin-bottom: 16px;
      font-size: 14px;
    }
    .secret {
      background: #101828;
      color: white;
      border-radius: 8px;
      padding: 12px;
      margin-top: 10px;
      display: none;
      word-break: break-all;
    }
    .bars { display: grid; gap: 9px; }
    .bar-row { display: grid; grid-template-columns: 110px 1fr 46px; gap: 10px; align-items: center; font-size: 13px; }
    .bar { height: 8px; background: #edf1f5; border-radius: 999px; overflow: hidden; }
    .bar i { display: block; height: 100%; background: var(--accent); border-radius: 999px; }
    .device-list { display: grid; gap: 9px; }
    .device-item { display: grid; grid-template-columns: 1fr auto; gap: 8px; padding: 10px; background: var(--panel2); border: 1px solid var(--line2); border-radius: 8px; }
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; }
    .rel-card { border-left: 4px solid var(--accent2); cursor: pointer; transition: transform .15s, box-shadow .15s; }
    .rel-card:hover { transform: translateY(-2px); }
    .rel-card h4 { margin: 0 0 7px; }
    .mini-bars { display: grid; gap: 6px; margin-top: 10px; }
    .timeline { max-height: 360px; overflow: auto; display: grid; gap: 8px; }
    .event {
      display: grid;
      grid-template-columns: 110px 1fr auto;
      gap: 10px;
      padding: 9px 10px;
      border: 1px solid var(--line2);
      border-radius: 8px;
      background: var(--panel2);
      font-size: 13px;
    }
    .graph-preview { min-height: 320px; position: relative; background: #f8fafc; border: 1px solid var(--line2); border-radius: 8px; overflow: hidden; }
    .node {
      position: absolute;
      transform: translate(-50%, -50%);
      padding: 6px 9px;
      border-radius: 999px;
      background: white;
      border: 1px solid var(--line);
      box-shadow: 0 4px 18px rgba(16,24,40,.08);
      font-size: 12px;
      white-space: nowrap;
    }
    .node.user { background: #101828; color: white; }
    .node.like { border-color: #abefc6; color: #027a48; }
    .node.dislike { border-color: #fecdca; color: #b42318; }
    .node.person { border-color: #b2ccff; color: #1849a9; }
    .empty { padding: 22px; text-align: center; color: var(--muted); background: white; border: 1px dashed var(--line); border-radius: 8px; }
    .toast {
      position: fixed;
      right: 22px;
      bottom: 22px;
      background: #101828;
      color: white;
      padding: 11px 14px;
      border-radius: 8px;
      box-shadow: var(--shadow);
      opacity: 0;
      transform: translateY(8px);
      pointer-events: none;
      transition: .18s;
      z-index: 20;
      max-width: 420px;
    }
    .toast.show { opacity: 1; transform: translateY(0); }
    @media (max-width: 960px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar { position: static; height: auto; }
      .side-foot { position: static; margin-top: 18px; }
      .grid.kpis, .grid.two, .grid.three { grid-template-columns: 1fr; }
      .form-grid { grid-template-columns: 1fr; }
      .topbar { padding: 0 16px; flex-wrap: wrap; height: auto; min-height: 64px; padding-top: 10px; padding-bottom: 10px; }
      .content { padding: 18px 16px 36px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">relationshape<span>后台控制台</span></div>
      <nav class="nav" id="nav"></nav>
      <div class="side-foot">
        <div>SQLite MVP</div>
        <div id="dbHint">runtime/backend.sqlite3</div>
      </div>
    </aside>
    <main class="main">
      <header class="topbar">
        <h1 id="pageTitle">Overview</h1>
        <div class="spacer"></div>
        <select class="select" id="tenantSelect"></select>
        <button class="btn" id="refreshBtn">刷新</button>
        <button class="btn primary" id="seedBtn">初始化演示数据</button>
      </header>
      <div class="content">
        <section class="section active" id="overview"></section>
        <section class="section" id="api-keys"></section>
        <section class="section" id="capabilities"></section>
        <section class="section" id="devices"></section>
        <section class="section" id="faq"></section>
        <section class="section" id="relationships"></section>
      </div>
    </main>
  </div>
  <div class="toast" id="toast"></div>

<script>
const NAV = [
  ["overview", "Overview", "总览"],
  ["api-keys", "API Keys", "密钥管理"],
  ["capabilities", "Capabilities", "能力配置"],
  ["devices", "Devices", "设备大屏"],
  ["faq", "FAQ", "FAQ 管理"],
  ["relationships", "Relationships", "关系可视化"],
];
let state = { selected: "overview", snapshot: null, relationships: null, relDetail: null };

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;" }[c]));
const fmt = (n) => n == null ? "—" : Number(n).toLocaleString();
const pct = (v) => v == null ? "—" : `${Math.round(v * 1000) / 10}%`;
const json = (url, opts={}) => fetch(url, opts).then(async r => {
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || body.error || r.statusText);
  return body;
});
function toast(msg) {
  $("toast").textContent = msg;
  $("toast").classList.add("show");
  setTimeout(() => $("toast").classList.remove("show"), 2600);
}
function icon(id) {
  const paths = {
    overview: "M4 13h6V4H4v9Zm10 7h6V4h-6v16ZM4 20h6v-5H4v5Z",
    "api-keys": "M15 7a4 4 0 1 0-3.46 5.99L4 20.5 6.5 23l2-2 2 2 2-2-2-2 3.5-3.5A4 4 0 0 0 15 7Zm0-2 2-2 2 2-2 2-2-2Z",
    capabilities: "M12 2 4 6v6c0 5 3.4 8.7 8 10 4.6-1.3 8-5 8-10V6l-8-4Zm-3 10 2 2 4-5 1.5 1.2L11.2 17 7.5 13.3 9 12Z",
    devices: "M6 4h12a2 2 0 0 1 2 2v8H4V6a2 2 0 0 1 2-2Zm-2 12h16v2H4v-2Zm5 4h6v2H9v-2Z",
    faq: "M4 5a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H9l-5 5V5Zm7 11h2v-2h-2v2Zm1-12a4 4 0 0 0-4 4h2a2 2 0 1 1 2 2c-1.7 0-3 1.3-3 3h2c0-.6.4-1 1-1a4 4 0 0 0 0-8Z",
    relationships: "M12 3a4 4 0 0 1 4 4 4 4 0 0 1-1.3 2.95l2.28 3.8A3.5 3.5 0 1 1 15.3 15l-2.27-3.8a4.4 4.4 0 0 1-2.06 0L8.7 15a3.5 3.5 0 1 1-1.68-1.25l2.28-3.8A4 4 0 0 1 12 3Z",
  };
  return `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="${paths[id]}"/></svg>`;
}
function setSection(id) {
  state.selected = id;
  document.querySelectorAll(".section").forEach(el => el.classList.toggle("active", el.id === id));
  document.querySelectorAll(".nav button").forEach(btn => btn.classList.toggle("active", btn.dataset.id === id));
  $("pageTitle").textContent = NAV.find(n => n[0] === id)?.[1] || "Overview";
  render();
}
function renderNav() {
  $("nav").innerHTML = NAV.map(([id, en, zh]) => `<button data-id="${id}" class="${state.selected===id?'active':''}">${icon(id)}<span>${zh}</span></button>`).join("");
  document.querySelectorAll(".nav button").forEach(btn => btn.onclick = () => setSection(btn.dataset.id));
}
async function load() {
  const tenant = $("tenantSelect").value;
  const qs = tenant ? `?tenant_id=${encodeURIComponent(tenant)}` : "";
  state.snapshot = await json(`/api/v1/admin/ui/snapshot${qs}`);
  renderTenantSelect();
  await loadRelationships();
  render();
}
async function loadRelationships() {
  state.relationships = await json("/api/v1/admin/relationships");
  const first = state.relationships.users?.[0];
  state.relDetail = first ? await json(`/api/v1/admin/relationships/${encodeURIComponent(first.user_id)}`) : null;
}
function renderTenantSelect() {
  const tenants = state.snapshot.tenants || [];
  $("tenantSelect").innerHTML = tenants.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  if (state.snapshot.selected_tenant_id) $("tenantSelect").value = state.snapshot.selected_tenant_id;
}
function render() {
  if (!state.snapshot) return;
  renderOverview();
  renderApiKeys();
  renderCapabilities();
  renderDevices();
  renderFaq();
  renderRelationships();
}
function empty(msg) { return `<div class="empty">${esc(msg)}</div>`; }
function kpi(label, value, hint="") {
  return `<div class="card kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="hint">${esc(hint)}</div></div>`;
}
function renderOverview() {
  const s = state.snapshot;
  const ov = s.overview;
  if (!ov) {
    $("overview").innerHTML = `<div class="section-head"><div><h2>总览</h2><p>还没有工作区数据。</p></div></div>${empty("点击右上角“初始化演示数据”，先生成一套可操作后台数据。")}`;
    return;
  }
  const rel = state.relationships?.aggregate || {};
  $("overview").innerHTML = `
    <div class="section-head"><div><h2>运营总览</h2><p>API 使用、设备活跃、FAQ 与关系状态放在同一张控制台。</p></div></div>
    <div class="grid kpis">
      ${kpi("24h 活跃 API Key", fmt(ov.active_api_keys_24h), "过去 24 小时有成功调用")}
      ${kpi("24h 活跃设备", fmt(ov.active_devices_24h), `${fmt(ov.total_devices)} 台总设备`)}
      ${kpi("24h 调用量", fmt(ov.calls_24h), `平均延迟 ${ov.avg_latency_ms_24h} ms`)}
      ${kpi("关系用户", fmt(rel.total_users || 0), `${fmt(rel.total_memories || 0)} 条可见记忆`)}
    </div>
    <div class="grid two" style="margin-top:14px">
      <div class="panel">
        <div class="panel-title"><h3>设备地区分布</h3><span>${fmt(ov.total_devices)} devices</span></div>
        ${bars(ov.device_regions || [], "region", "devices")}
      </div>
      <div class="panel">
        <div class="panel-title"><h3>用户问题排行榜</h3><span>30 天</span></div>
        ${table(["问题", "次数"], (ov.top_questions_30d || []).map(q => [q.normalized_question || "—", q.count]))}
      </div>
    </div>
    <div class="grid three" style="margin-top:14px">
      <div class="panel"><div class="panel-title"><h3>留存</h3></div>
        <div class="grid two">
          ${kpi("1 个月留存", pct(ov.retention_30d?.rate), `${fmt(ov.retention_30d?.retained)} / ${fmt(ov.retention_30d?.cohort)}`)}
          ${kpi("3 个月留存", pct(ov.retention_90d?.rate), `${fmt(ov.retention_90d?.retained)} / ${fmt(ov.retention_90d?.cohort)}`)}
        </div>
      </div>
      <div class="panel"><div class="panel-title"><h3>能力配置</h3><span>${fmt(s.capability_configs.length)} configs</span></div>${providerSummary(s.capability_configs)}</div>
      <div class="panel"><div class="panel-title"><h3>FAQ 状态</h3><span>${fmt(s.faq_items.length)} items</span></div>${faqSummary(s.faq_items)}</div>
    </div>`;
}
function bars(rows, labelKey, valueKey) {
  if (!rows.length) return empty("暂无数据");
  const max = Math.max(...rows.map(r => Number(r[valueKey] || 0)), 1);
  return `<div class="bars">${rows.map(r => `<div class="bar-row"><span>${esc(r[labelKey] || "未设置")}</span><div class="bar"><i style="width:${Math.max(4, Number(r[valueKey] || 0) / max * 100)}%"></i></div><b>${fmt(r[valueKey])}</b></div>`).join("")}</div>`;
}
function table(headers, rows) {
  if (!rows.length) return empty("暂无数据");
  return `<table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
function providerSummary(configs) {
  if (!configs.length) return empty("暂无能力配置");
  return `<div class="bars">${configs.slice(0,4).map(c => `<div class="device-item"><div><b>${esc(c.name)}</b><div class="muted">ASR ${esc(c.asr_provider)} / LLM ${esc(c.llm_provider)} / TTS ${esc(c.tts_provider)}</div></div><span class="tag green">${c.memory?.enabled ? "memory on" : "memory off"}</span></div>`).join("")}</div>`;
}
function faqSummary(items) {
  if (!items.length) return empty("暂无 FAQ");
  const enabled = items.filter(i => i.enabled).length;
  return `<div class="grid two">${kpi("启用", enabled, "enabled")} ${kpi("停用", items.length - enabled, "disabled")}</div>`;
}
function renderApiKeys() {
  const s = state.snapshot;
  $("api-keys").innerHTML = `
    <div class="section-head"><div><h2>API Key 管理</h2><p>创建、查看、吊销和限制每个客户或设备群的访问入口。</p></div></div>
    <div class="grid two">
      <div class="panel">
        <div class="panel-title"><h3>现有 Key</h3><span>${fmt(s.api_keys.length)} keys</span></div>
        ${table(["名称", "Preview", "状态", "权限", "最后使用"], s.api_keys.map(k => [
          `<b>${esc(k.name)}</b>`,
          `<span class="mono">${esc(k.preview)}</span>`,
          `<span class="tag ${k.status === "active" ? "green" : "red"}">${esc(k.status)}</span>`,
          esc(k.scopes.join(", ")),
          esc(k.last_used_at || "—")
        ]))}
      </div>
      <div class="panel">
        <div class="panel-title"><h3>创建 Key</h3><span>明文只显示一次</span></div>
        <form id="keyForm" class="form-grid">
          <label class="wide">名称<input name="name" value="新设备 API Key"></label>
          <label>每分钟限流<input name="rate" type="number" value="120"></label>
          <label>月额度<input name="quota" type="number" value="500000"></label>
          <label class="wide">Scopes<input name="scopes" value="runtime:invoke,faq:read"></label>
          <button class="btn primary wide" type="submit">创建 API Key</button>
        </form>
        <div class="secret mono" id="newKeySecret"></div>
      </div>
    </div>`;
  $("keyForm").onsubmit = createKey;
}
async function createKey(e) {
  e.preventDefault();
  const fd = new FormData(e.currentTarget);
  const users = state.snapshot.users || [];
  const owner = state.snapshot.overview?.tenant?.owner_user_id || users[0]?.id;
  if (!state.snapshot.selected_tenant_id || !owner) return toast("请先初始化工作区");
  const created = await json("/api/v1/admin/api-keys", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      tenant_id: state.snapshot.selected_tenant_id,
      created_by: owner,
      name: fd.get("name"),
      scopes: String(fd.get("scopes")).split(",").map(s => s.trim()).filter(Boolean),
      rate_limit_per_minute: Number(fd.get("rate") || 0),
      monthly_quota: Number(fd.get("quota") || 0),
    }),
  });
  $("newKeySecret").style.display = "block";
  $("newKeySecret").textContent = created.api_key;
  toast("API Key 已创建，明文只显示在当前页面");
  await load();
}
function renderCapabilities() {
  const s = state.snapshot;
  const keyOptions = s.api_keys.map(k => `<option value="${esc(k.id)}">${esc(k.name)} / ${esc(k.preview)}</option>`).join("");
  $("capabilities").innerHTML = `
    <div class="section-head"><div><h2>能力配置</h2><p>给每个 API Key 绑定 ASR、LLM、TTS、性格、长期记忆和安全策略。</p></div></div>
    <div class="grid two">
      <div class="panel">
        <div class="panel-title"><h3>配置列表</h3><span>${fmt(s.capability_configs.length)} configs</span></div>
        ${table(["名称", "Key", "Provider", "Memory"], s.capability_configs.map(c => [
          `<b>${esc(c.name)}</b>`,
          `<span class="mono">${esc(c.api_key_id || "tenant default")}</span>`,
          `ASR ${esc(c.asr_provider)}<br>LLM ${esc(c.llm_provider)}<br>TTS ${esc(c.tts_provider)}`,
          c.memory?.enabled ? `<span class="tag green">on</span>` : `<span class="tag gray">off</span>`
        ]))}
      </div>
      <div class="panel">
        <div class="panel-title"><h3>保存配置</h3><span>upsert by API Key</span></div>
        <form id="configForm" class="form-grid">
          <label class="wide">绑定 API Key<select name="api_key_id">${keyOptions}</select></label>
          <label>配置名<input name="name" value="默认能力配置"></label>
          <label>ASR<select name="asr"><option>volcengine</option><option>aliyun</option><option>openai</option><option>deepgram</option></select></label>
          <label>LLM<select name="llm"><option>deepseek</option><option>openai</option><option>claude</option><option>qwen</option><option>doubao</option></select></label>
          <label>TTS<select name="tts"><option>azure</option><option>volcengine</option><option>elevenlabs</option><option>aliyun</option></select></label>
          <label class="wide">人格 JSON<textarea name="personality">{"tone":"warm","energy":"medium","age_band":"child"}</textarea></label>
          <label class="wide">记忆 JSON<textarea name="memory">{"enabled":true,"episodic_cap":400,"recall_top_k":3}</textarea></label>
          <button class="btn primary wide" type="submit">保存能力配置</button>
        </form>
      </div>
    </div>`;
  $("configForm").onsubmit = saveConfig;
}
async function saveConfig(e) {
  e.preventDefault();
  const fd = new FormData(e.currentTarget);
  await json("/api/v1/admin/capability-configs", {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      tenant_id: state.snapshot.selected_tenant_id,
      api_key_id: fd.get("api_key_id"),
      name: fd.get("name"),
      asr_provider: fd.get("asr"),
      llm_provider: fd.get("llm"),
      tts_provider: fd.get("tts"),
      personality: JSON.parse(fd.get("personality")),
      memory: JSON.parse(fd.get("memory")),
      safety: {"child_mode": true},
    }),
  });
  toast("能力配置已保存");
  await load();
}
function renderDevices() {
  const s = state.snapshot;
  $("devices").innerHTML = `
    <div class="section-head"><div><h2>设备大屏</h2><p>设备活跃、地区、使用时长、调用事件和问题事件。</p></div></div>
    <div class="grid kpis">
      ${kpi("总设备", s.overview ? fmt(s.overview.total_devices) : 0, "registered")}
      ${kpi("24h 活跃", s.overview ? fmt(s.overview.active_devices_24h) : 0, "last seen")}
      ${kpi("24h 调用", s.overview ? fmt(s.overview.calls_24h) : 0, "usage events")}
      ${kpi("24h 成本", s.overview ? fmt(s.overview.cost_micros_24h) : 0, "micros")}
    </div>
    <div class="grid two" style="margin-top:14px">
      <div class="panel"><div class="panel-title"><h3>设备列表</h3><span>${fmt(s.devices.length)} devices</span></div>${deviceList(s.devices)}</div>
      <div class="panel"><div class="panel-title"><h3>最近调用</h3><span>20 events</span></div>${table(["时间", "设备", "Provider", "延迟"], s.usage_events.map(e => [esc(e.created_at), esc(e.external_device_id || "—"), esc(e.provider || "—"), `${esc(e.latency_ms || "—")} ms`]))}</div>
    </div>`;
}
function deviceList(devices) {
  if (!devices.length) return empty("暂无设备");
  return `<div class="device-list">${devices.map(d => `<div class="device-item"><div><b>${esc(d.external_device_id)}</b><div class="muted">${esc(d.region || "未设置地区")} / ${esc(d.model || "unknown")} / ${fmt(d.total_usage_seconds)}s</div></div><span class="tag green">${esc(d.last_seen_at || "—")}</span></div>`).join("")}</div>`;
}
function renderFaq() {
  const s = state.snapshot;
  const baseOptions = s.faq_bases.map(b => `<option value="${esc(b.id)}">${esc(b.name)}</option>`).join("");
  $("faq").innerHTML = `
    <div class="section-head"><div><h2>FAQ 管理</h2><p>维护知识库条目，并观察用户问题排行与命中趋势。</p></div></div>
    <div class="grid two">
      <div class="panel"><div class="panel-title"><h3>FAQ 条目</h3><span>${fmt(s.faq_items.length)} items</span></div>${table(["问题", "答案", "标签", "状态"], s.faq_items.map(i => [esc(i.question), esc(i.answer), esc((i.tags || []).join(", ")), i.enabled ? `<span class="tag green">enabled</span>` : `<span class="tag gray">disabled</span>`]))}</div>
      <div class="panel">
        <div class="panel-title"><h3>新增或更新条目</h3></div>
        <form id="faqForm" class="form-grid">
          <label class="wide">FAQ Base<select name="faq_base_id">${baseOptions}</select></label>
          <label class="wide">问题<input name="question" value="如何重启设备？"></label>
          <label class="wide">答案<textarea name="answer">长按背部按键五秒，等待指示灯重新亮起。</textarea></label>
          <label class="wide">Tags<input name="tags" value="device,reset"></label>
          <button class="btn primary wide" type="submit">保存 FAQ</button>
        </form>
      </div>
    </div>
    <div class="panel" style="margin-top:14px"><div class="panel-title"><h3>最近问题</h3><span>runtime question events</span></div>${table(["时间", "设备", "问题", "归一化"], s.question_events.map(q => [esc(q.created_at), esc(q.external_device_id || "—"), esc(q.question_text), esc(q.normalized_question)]))}</div>`;
  $("faqForm").onsubmit = saveFaq;
}
async function saveFaq(e) {
  e.preventDefault();
  const fd = new FormData(e.currentTarget);
  if (!fd.get("faq_base_id")) return toast("请先初始化 FAQ Base");
  await json("/api/v1/admin/faq-items", {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      faq_base_id: fd.get("faq_base_id"),
      question: fd.get("question"),
      answer: fd.get("answer"),
      tags: String(fd.get("tags")).split(",").map(s => s.trim()).filter(Boolean),
      enabled: true,
    }),
  });
  toast("FAQ 已保存");
  await load();
}
function renderRelationships() {
  const rel = state.relationships;
  const detail = state.relDetail;
  $("relationships").innerHTML = `
    <div class="section-head"><div><h2>关系可视化</h2><p>融合 intelligent-bardeen 分支的用户关系入口：关系阶段、时间线、记忆图谱。</p></div><span class="tag gray">${esc(rel?.state_dir || "")}</span></div>
    ${!rel?.users?.length ? empty("暂无关系状态文件。运行 demo/simulate.py --chat 或现有对话后，这里会展示用户关系。") : `
      <div class="grid two">
        <div class="panel">
          <div class="panel-title"><h3>用户关系画廊</h3><span>${fmt(rel.aggregate.total_users)} users</span></div>
          <div class="cards">${rel.users.map(u => relCard(u)).join("")}</div>
        </div>
        <div class="panel">
          <div class="panel-title"><h3>${esc(detail?.user?.name || "关系详情")}</h3><span>${esc(detail?.user?.stage_zh || "")}</span></div>
          ${detail ? relationshipDetail(detail) : empty("选择一个用户")}
        </div>
      </div>`}`;
  document.querySelectorAll(".rel-card").forEach(el => el.onclick = async () => {
    state.relDetail = await json(`/api/v1/admin/relationships/${encodeURIComponent(el.dataset.user)}`);
    renderRelationships();
  });
}
function relCard(u) {
  return `<div class="card rel-card" data-user="${esc(u.user_id)}"><h4>${esc(u.name)}</h4><div class="muted">${esc(u.user_id)}</div><div style="margin-top:8px"><span class="tag green">${esc(u.stage_zh)}</span></div><div class="mini-bars">${mini("信任", u.trust, 100)}${mini("亲密", u.closeness, 100)}</div><div class="muted" style="margin-top:8px">${fmt(u.episodes)} memories / ${fmt(u.people)} people / ${fmt(u.inside_jokes)} jokes</div></div>`;
}
function mini(label, value, max) {
  return `<div class="bar-row" style="grid-template-columns:46px 1fr 38px"><span>${label}</span><div class="bar"><i style="width:${Math.max(3, Math.min(100, Number(value || 0) / max * 100))}%"></i></div><b>${esc(value)}</b></div>`;
}
function relationshipDetail(d) {
  return `
    <div class="grid two">
      ${kpi("认识天数", fmt(d.days_known), `${fmt(d.user.sessions)} sessions`)}
      ${kpi("深表露", fmt(d.ledger.deep), `${fmt(d.ledger.disclosures)} disclosures`)}
    </div>
    <div class="panel-title" style="margin-top:14px"><h3>记忆图谱预览</h3><span>${fmt(d.graph.nodes.length)} nodes</span></div>
    ${graphPreview(d.graph.nodes)}
    <div class="panel-title" style="margin-top:14px"><h3>时间线</h3><span>${fmt(d.timeline.length)} events</span></div>
    <div class="timeline">${d.timeline.slice(0,18).map(e => `<div class="event"><span class="muted">${esc(e.t || "")}</span><div><b>${esc(e.label || e.kind || "event")}</b><div class="muted">${esc(e.detail || "")}</div></div><span>${esc(e.trust ?? "")}</span></div>`).join("") || empty("暂无时间线")}</div>`;
}
function graphPreview(nodes) {
  if (!nodes.length) return empty("暂无图谱节点");
  const cx = 50, cy = 50, r = 34;
  return `<div class="graph-preview">${nodes.map((n, i) => {
    const angle = i === 0 ? 0 : (i - 1) / Math.max(1, nodes.length - 1) * Math.PI * 2;
    const x = i === 0 ? cx : cx + Math.cos(angle) * r;
    const y = i === 0 ? cy : cy + Math.sin(angle) * r;
    return `<div class="node ${esc(n.type)}" style="left:${x}%;top:${y}%">${esc(n.label)}</div>`;
  }).join("")}</div>`;
}
$("refreshBtn").onclick = () => load().then(() => toast("已刷新"));
$("seedBtn").onclick = async () => {
  state.snapshot = await json("/api/v1/admin/demo/seed", {method: "POST"});
  renderTenantSelect();
  await loadRelationships();
  render();
  toast("演示工作区已初始化");
};
$("tenantSelect").onchange = load;
renderNav();
load().catch(err => {
  console.error(err);
  $("overview").innerHTML = `<div class="notice">加载失败：${esc(err.message)}</div>`;
});
</script>
</body>
</html>
"""
