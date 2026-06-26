/* AI 最强大脑挑战机 · 设备端 —— 拓麻歌子式养成（纯 API 客户端，零业务逻辑）。
 * 宠物常驻屏幕：蛋阶段每天 5 道题 = 它的 5 口"渴求"，答题 = 喂灵材 → 蛋长大 → 第7天破壳成专属宠。
 * 判分/喂养/破壳/物种全在服务端，浏览器只渲染状态、发动作、播动画。
 */
"use strict";
const api = async (m, p, b) => (await fetch(p, { method: m, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined })).json();
const get = (p) => api("GET", p), post = (p, b) => api("POST", p, b);
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const DOMAIN_COLOR = { li: "#4aa3ff", wen: "#b07bff", bo: "#46c98b" };
const ELEM_COLOR = { "晶": "#4aa3ff", "声": "#b07bff", "风": "#46c98b", "焰": "#ff6b5a", "光": "#ffc24a" };
const state = { cid: null, home: null, clock: null };
const view = () => $("#view"), screen = () => $(".screen");
const setElem = (c) => screen().style.setProperty("--elem", c || "#ffb86b");

function confetti(n = 90) {
  const fx = $("#fx"), cols = ["#ff6b81", "#ffb12e", "#6c5ce7", "#46c98b", "#4aa3ff", "#ff8a5b"];
  for (let i = 0; i < n; i++) {
    const s = document.createElement("span"); s.className = "confetti";
    s.style.left = Math.random() * 100 + "%"; s.style.background = cols[i % cols.length];
    s.style.animationDuration = (1 + Math.random() * 1.2) + "s"; s.style.animationDelay = (Math.random() * .3) + "s";
    s.style.transform = `rotate(${Math.random() * 360}deg)`; fx.appendChild(s); setTimeout(() => s.remove(), 2600);
  }
}
async function typewriter(node, text, sp = 24) { node.textContent = ""; for (let i = 0; i < text.length; i++) { node.textContent = text.slice(0, i + 1); await sleep(sp); } }
function ringSVG(pct) {
  const r = 104, c = 2 * Math.PI * r, off = c * (1 - Math.max(0, Math.min(100, pct)) / 100);
  return `<svg class="ring" viewBox="0 0 230 230"><circle cx="115" cy="115" r="${r}" fill="none" stroke="#ececf5" stroke-width="9"/>
    <circle cx="115" cy="115" r="${r}" fill="none" stroke="var(--elem)" stroke-width="9" stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/></svg>`;
}
const slot = (filled) => `<span class="fslot ${filled ? "on" : ""}"></span>`;

/* ---------- 数据 ---------- */
async function loadHome() {
  if (!state.cid) { state.home = null; return; }
  const h = await get(`/api/child/${state.cid}/home`);
  state.home = h.error ? null : h;
  if (state.home) {
    const cv = state.home.cultivation;
    setElem(cv.stage === "egg" ? DOMAIN_COLOR[cv.dominant] : ELEM_COLOR[(cv.species || {}).elem]);
  }
}
async function refreshClock() { state.clock = await get("/api/clock"); }

function renderHome() {
  const v = view(); v.classList.remove("slide"); void v.offsetWidth; v.classList.add("slide");
  if (!state.cid) { v.innerHTML = `<div class="empty">还没有设备数据。<br>点右侧「注入演示」。</div>`; return; }
  if (state.home.cultivation.stage === "egg") renderEgg(); else renderYouth();
}

/* ---------- 蛋·首页 ---------- */
function renderEgg() {
  const h = state.home, cv = h.cultivation, t = h.today;
  const left = cv.days_to_hatch - cv.egg_day;
  const cracking = cv.egg_day >= 6 ? "cracking" : "";
  const full = t.done >= t.total;
  view().innerHTML = `
    <div class="hometop"><div class="petname2">🥚 神秘的蛋</div><button class="manual" data-act="manual">📒 手册</button></div>
    <div class="greet"><div class="coach">🥚</div><div class="greet-bubble" id="cravingline"></div></div>
    <div class="hero">
      <div class="ringwrap">${ringSVG(100 * cv.egg_day / cv.days_to_hatch)}
        <div class="petorb egg"><div class="petemoji ${cracking}" data-act="poke">🥚</div></div>
        <div class="ringpct">孵化 ${cv.egg_day}/${cv.days_to_hatch} 天</div>
      </div>
      <div class="hero-name">孵化中…</div>
      <div class="hero-realm">${left > 0 ? `再喂养 ${left} 天，就要破壳啦！` : "今天就要破壳！"}</div>
    </div>
    <div class="feedslots">${Array.from({ length: t.total }, (_, i) => slot(i < t.done)).join("")}<span class="fslab">今日灵材 ${t.done}/${t.total}</span></div>
    ${full
      ? `<button class="cta done">今天喂饱啦 🌙 它在安睡<sub>明天再来，它会更大一点</sub></button>`
      : `<button class="cta" data-act="feed">喂养它<sub>它饿了，去采集今天的灵材（${t.total - t.done} 口）</sub></button>`}
    <div class="quickchips">
      <span class="qchip">🔥 连续 <b>${h.streak}</b> 天</span>
      <span class="qchip">📅 ${esc((state.clock && state.clock.day) || "")}</span>
    </div>`;
  typewriter($("#cravingline"), full ? "今天吃得好饱…谢谢你，明天见~" : cv.craving);
}

/* ---------- 幼年·首页（破壳后） ---------- */
function renderYouth() {
  const h = state.home, cv = h.cultivation, sp = cv.species, t = h.today;
  const full = t.done >= t.total;
  const mats = Object.values(cv.materials).reduce((a, b) => a + b, 0);
  view().innerHTML = `
    <div class="hometop"><div class="petname2">${sp.emoji} ${esc(sp.name)}</div><button class="manual" data-act="manual">📒 手册</button></div>
    <div class="greet"><div class="coach">${sp.emoji}</div><div class="greet-bubble" id="cravingline"></div></div>
    <div class="hero">
      <div class="ringwrap">${ringSVG(t.total ? 100 * t.done / t.total : 0)}
        <div class="petorb"><div class="petemoji" data-act="poke">${sp.emoji}</div></div>
        <div class="ringpct">今日修炼 ${t.done}/${t.total}</div>
      </div>
      <div class="hero-name">${esc(sp.name)} · 幼年期</div>
      <div class="hero-realm">${esc(sp.elem)}系 · ${esc(sp.blurb)}</div>
    </div>
    <div class="pods">
      <div class="pod"><b>${h.streak}</b><span>🔥 连续天</span></div>
      <div class="pod"><b>${mats}</b><span>🧪 灵材</span></div>
      <div class="pod"><b>${h.stars}</b><span>⭐ 智慧星</span></div>
    </div>
    ${full
      ? `<button class="cta done">今日修炼完成 🌙<sub>它正在消化今天的灵材</sub></button>`
      : `<button class="cta" data-act="feed">今天的修炼<sub>陪它练 ${t.total - t.done} 关，离筑基更近一步</sub></button>`}
    <button class="bump-cta" data-act="bump">⚡ 碰一碰 · 和好友切磋</button>
    <div class="locktease">🔒 筑基期 · 积累修为与灵药后开启</div>`;
  typewriter($("#cravingline"), full ? "今天练得真好，我能感觉到力量在长~" : "今天也一起修炼吧，我想快点长大！");
}

/* ---------- 喂养流程（5 道题 = 5 口渴求）---------- */
let flow = null;
function startFeeding() {
  const t = state.home.today, steps = t.challenges.filter((c) => !c.answered);
  if (!steps.length) return;
  flow = { steps, i: 0, hatch: null };
  const node = document.createElement("div"); node.className = "feed"; node.id = "feed"; screen().appendChild(node);
  renderFeedStep();
}
function feedDots() {
  const t = state.home.today, base = t.total - flow.steps.length;
  return Array.from({ length: t.total }, (_, k) => `<span class="dot ${k < base + flow.i ? "done" : (k === base + flow.i ? "active" : "")}"></span>`).join("");
}
async function renderFeedStep() {
  const c = flow.steps[flow.i], node = $("#feed");
  const isEgg = state.home.cultivation.stage === "egg";
  node.style.setProperty("--mc", c.color || "#6c5ce7");
  node.innerHTML = `
    <div class="feed-top"><button class="feed-close" data-fx="close">✕</button><div class="dots">${feedDots()}</div><div style="width:34px"></div></div>
    <div class="feed-egg" id="feedEgg">${isEgg ? "🥚" : (state.home.cultivation.species.emoji)}</div>
    <div class="feed-craving">它说：<span id="craveq"></span></div>
    <div class="feed-q"><div class="qlabel"><i style="background:${c.color}"></i>第 ${flow.i + 1} 口 · ${esc(c.material)}（${esc(c.domain_zh)}灵材）</div>
      <div class="qprompt">${esc(c.prompt)}</div></div>
    <div class="feed-answer">
      <textarea id="fans" placeholder="${c.score_mode === "effort" ? "把想法说给它听…（认真说就有营养，不打分对错）" : "写下你的答案，喂给它…"}"></textarea>
      <button class="feed-go" id="fgo" disabled>喂给它 ▸</button>
    </div>`;
  $(".feed-close").onclick = () => closeFeed();
  const ta = $("#fans"), go = $("#fgo");
  ta.addEventListener("input", () => go.disabled = !ta.value.trim());
  go.onclick = () => submitFeed();
  await typewriter($("#craveq"), c.craving, 22);
}
async function submitFeed() {
  const c = flow.steps[flow.i], val = $("#fans").value.trim(); $("#fgo").disabled = true;
  const r = await post(`/api/child/${state.cid}/answer`, { cid: c.cid, answer: val });
  if (r.error) { alert(r.detail || r.error); return; }
  state.home = r.home; (r.events || []).forEach((e) => { if (e.kind === "hatch") flow.hatch = e; });
  await flyMaterial(r.outcome);
  showFeedback(r.outcome);
}
async function flyMaterial(o) {
  const node = $("#feed"), egg = $("#feedEgg");
  const orb = document.createElement("div"); orb.className = "orb";
  orb.style.background = (o.fed && o.fed.color) || "#c7b3ff";
  orb.textContent = o.fed ? "" : "✨"; node.appendChild(orb);
  await sleep(640); orb.remove();
  egg.classList.add("eat"); setTimeout(() => egg.classList.remove("eat"), 360);
}
function showFeedback(o) {
  const correct = o.correct, kind = correct === true ? "ok" : (correct === false ? "no" : "eff");
  const emoji = correct === true ? "😋" : (correct === false ? "🤔" : "💛");
  const title = o.fed ? `喂到了！+1 颗${esc(o.fed.material)}` : "+ 成长";
  if (correct === true) confetti(40);
  const showExplain = o.explain && correct !== true;
  const last = flow.i + 1 >= flow.steps.length;
  const fb = document.createElement("div"); fb.className = "fb-card";
  fb.innerHTML = `
    <div class="fb-emoji">${emoji}</div>
    <div class="fb-title ${kind}">${title}</div>
    <div class="fb-box">${esc(o.feedback)}</div>
    ${showExplain ? `<div class="fb-box"><span class="lab">📘 讲解：</span>${esc(o.explain)}</div>` : ""}
    ${o.extend ? `<div class="fb-box"><span class="lab">💡 再想一步：</span>${esc(o.extend)}</div>` : ""}
    <button class="fb-next">${last ? "看看它 ✨" : "下一口 →"}</button>`;
  $("#feed").appendChild(fb);
  $(".fb-next").onclick = () => { fb.remove(); flow.i++; if (flow.i < flow.steps.length) renderFeedStep(); else finishFeeding(); };
}
function closeFeed() { const n = $("#feed"); if (n) n.remove(); flow = null; loadHome().then(renderHome); }
function finishFeeding() {
  const n = $("#feed"); if (n) n.remove();
  if (flow.hatch) hatchCinematic(flow.hatch); else dailyCele();
}

/* ---------- 每日喂饱小结 ---------- */
function dailyCele() {
  const cv = state.home.cultivation, isEgg = cv.stage === "egg";
  const node = document.createElement("div"); node.className = "cele"; node.id = "cele"; screen().appendChild(node);
  const left = cv.days_to_hatch - cv.egg_day;
  node.innerHTML = `
    <div class="cele-pet grow">${isEgg ? "🥚" : cv.species.emoji}</div>
    <div class="cele-title">${isEgg ? "它吃饱啦！" : "今天修炼完成！"}</div>
    <div class="cele-sub">${isEgg ? `今天又孵化了一天 · 第 ${cv.egg_day}/${cv.days_to_hatch} 天` : "它消化了今天的灵材，离筑基更近了"}</div>
    ${isEgg ? `<div class="cele-line">${left > 0 ? `再喂养 ${left} 天，就要破壳啦！` : ""}</div>` : ""}
    <button class="cele-btn">回去看看它</button>`;
  confetti(80);
  $(".cele-btn").onclick = () => { node.remove(); loadHome().then(renderHome); };
}

/* ---------- 破壳！---------- */
function hatchCinematic(ev) {
  const sp = ev.species_info, cv = state.home.cultivation;
  const node = document.createElement("div"); node.className = "hatch"; node.id = "hatch"; screen().appendChild(node);
  node.innerHTML = `<div class="hatch-egg cracking" id="hegg">🥚</div>`;
  setElem(ELEM_COLOR[sp.elem]);
  setTimeout(() => {
    node.innerHTML = `<div class="hatch-reveal">
      <div class="hatch-burst"></div>
      <div class="hatch-pet">${sp.emoji}</div>
      <div class="hatch-title">🎉 破壳了！</div>
      <div class="hatch-name">「${esc(sp.name)}」</div>
      <div class="hatch-blurb">${esc(sp.blurb)}</div>
      <div class="hatch-why">因为这 7 天，你喂它最多的是 <b>${esc(cv.dominant_zh)}</b> 灵材</div>
      <button class="cele-btn">认识它 →</button>`;
    confetti(160);
    $(".cele-btn").onclick = () => { node.remove(); loadHome().then(renderHome); };
  }, 1700);
}

/* ---------- 手册 ---------- */
function showManual() {
  const h = state.home, cv = h.cultivation;
  const mat = cv.materials, aff = cv.affinity;
  const bar = (d, zh) => `<div class="abrow"><div class="an">${zh}<i>${mat[d] || 0} 颗</i></div>
    <div class="track"><i style="width:${Math.round((aff[d] || 0) * 100)}%;background:${DOMAIN_COLOR[d]}"></i></div>
    <div class="av">${Math.round((aff[d] || 0) * 100)}</div></div>`;
  const grown = cv.stage !== "egg";
  const sheet = document.createElement("div"); sheet.className = "sheet";
  sheet.innerHTML = `<div class="sheet-card">
    <h3>📒 成长手册</h3>
    <div class="section-t">🧪 灵材库 · 本命亲和</div>
    <div class="abils">${bar("li", "理科")}${bar("wen", "文科")}${bar("bo", "博物")}</div>
    <div class="manual-note">它最亲近 <b>${esc(cv.dominant_zh)}</b> 灵材${grown ? "——破壳时正是由它决定了物种。" : "（破壳成哪种宠，就看这个）。"}</div>
    ${grown ? `<div class="section-t">🌟 修为</div><div class="abils">${h.abilities.map((a) => `<div class="abrow"><div class="an">${esc(a.ability_zh)}<i>${esc(a.realm)}</i></div><div class="track"><i style="width:${a.level}%"></i></div><div class="av">lv${a.level}</div></div>`).join("")}</div>` : `<div class="manual-note">破壳后解锁能力面板与境界。</div>`}
    <button class="parent-btn" data-act="parent">👪 家长报告</button>
    <button class="sheet-close">收起</button></div>`;
  screen().appendChild(sheet);
  $(".sheet-close").onclick = () => sheet.remove();
  $(".parent-btn").onclick = () => { sheet.remove(); showParent(); };
  sheet.onclick = (e) => { if (e.target === sheet) sheet.remove(); };
}
async function showParent() {
  const rep = await get(`/api/child/${state.cid}/report`);
  const rows = rep.abilities.map((a) => `<tr><td>${esc(a.ability_zh)} <b style="color:var(--elem)">${esc(a.realm)}</b></td><td>${a.week_ago}</td><td>${a.now}</td><td class="${a.delta > 0 ? "up" : ""}">${a.delta > 0 ? "+" + a.delta : a.delta}</td><td>${a.accuracy == null ? "—" : a.accuracy + "%"}</td></tr>`).join("");
  const hls = (rep.highlights || []).map((x) => `<div class="rep-hl"><div class="q">${esc(x.prompt)}</div>“${esc(x.answer)}”</div>`).join("") || '<span style="color:var(--muted);font-size:12px">本周还没有高光片段</span>';
  const sheet = document.createElement("div"); sheet.className = "sheet";
  sheet.innerHTML = `<div class="sheet-card">
    <h3>👪 ${esc(rep.name)} · 本周成长报告</h3>
    <table class="rep-tab"><tr><th>能力·境界</th><th>周初</th><th>现在</th><th>变化</th><th>正确率</th></tr>${rows}</table>
    <div class="section-t" style="margin:2px 0 8px">✨ 本周亮点</div>${hls}
    <div class="rep-note">${esc(rep.honest_note)}</div>
    <button class="sheet-close">收起</button></div>`;
  screen().appendChild(sheet);
  $(".sheet-close").onclick = () => sheet.remove();
  sheet.onclick = (e) => { if (e.target === sheet) sheet.remove(); };
}

/* ---------- 碰一碰（幼年后）---------- */
async function startBattle() {
  const kids = (await get("/api/children")).children || [];
  const others = kids.filter((k) => k.child_id !== state.cid);
  if (!others.length) { alert("需要另一台设备（右侧「注入演示」会多一个孩子）"); return; }
  const res = await post("/api/battle", { a: state.cid, b: others[0].child_id });
  if (res.error) { alert(res.detail || res.error); return; }
  await playBattle(res);
}
function fHTML(side, name, rank, stats) {
  return `<div class="bf"><div class="bn">${esc(name)}</div><div class="brk">${esc(rank)} · 战力${stats.battle_power}</div>
    <div class="bhp"><i id="bhp${side}" style="width:100%"></i></div><div class="bpet" id="bpet${side}">🐲</div></div>`;
}
async function playBattle(res) {
  const node = document.createElement("div"); node.className = "bt"; node.id = "bt"; screen().appendChild(node);
  node.innerHTML = `<button class="bt-skip">跳过 »</button>
    <div class="bt-fighters">${fHTML("a", res.a_name, res.a_rank, res.a_stats)}<div class="bvs">VS</div>${fHTML("b", res.b_name, res.b_rank, res.b_stats)}</div>
    ${res.friendly ? '<div class="bt-friendly">🤝 友谊赛：抹平差距、点到为止，输了不掉成长值</div>' : ""}<div id="bt-result"></div>`;
  let skip = false; $(".bt-skip").onclick = () => skip = true;
  for (const e of res.log) {
    if (skip) break;
    $("#bpet" + e.actor).classList.add("lunge-" + e.actor);
    $("#bhpa").style.width = e.pct_a + "%"; $("#bhpb").style.width = e.pct_b + "%";
    const d = document.createElement("div"); let lbl = "-" + e.dmg, cls = "dmg";
    if (e.move === "special") { lbl = "绝招! -" + e.dmg; cls += " special"; }
    if (e.crit) { lbl = "暴击! -" + e.dmg; cls += " crit"; node.classList.add("shake"); }
    d.className = cls; d.textContent = lbl; d.style.left = (e.foe === "a" ? 22 : 64) + "%"; d.style.top = "42%"; node.appendChild(d);
    setTimeout(() => { $("#bpet" + e.actor).classList.remove("lunge-" + e.actor); node.classList.remove("shake"); }, 260);
    setTimeout(() => d.remove(), 900); await sleep(skip ? 0 : 460);
  }
  const last = res.log[res.log.length - 1] || { pct_a: 100, pct_b: 100 };
  $("#bhpa").style.width = last.pct_a + "%"; $("#bhpb").style.width = last.pct_b + "%";
  $("#bt-result").innerHTML = `<div class="bt-result"><div class="bnarr">${res.ko ? "💥 " : "🏁 "}${esc(res.narration)}</div>
    <div class="brew">你：+${res.a_reward.stars}⭐ +${res.a_reward.growth}成长</div>
    <div class="bt-actions"><button class="again">再碰一碰</button><button class="close">收起</button></div></div>`;
  if (res.winner === "a" && !res.friendly) confetti(70);
  $(".again").onclick = () => { node.remove(); startBattle(); };
  $(".close").onclick = () => { node.remove(); loadHome().then(renderHome); };
}

/* ---------- 事件 ---------- */
function wire() {
  view().addEventListener("click", (e) => {
    const a = e.target.closest("[data-act]"); if (!a) return;
    const act = a.dataset.act;
    if (act === "feed") startFeeding();
    else if (act === "manual") showManual();
    else if (act === "bump") startBattle();
    else if (act === "poke") { a.classList.remove("poke"); void a.offsetWidth; a.classList.add("poke"); }
  });
  $("#who").addEventListener("change", async (e) => { state.cid = e.target.value || null; await loadHome(); renderHome(); });
  $("#adv").onclick = async () => { await post("/api/clock/advance", { days: 1 }); await refreshClock(); await loadHome(); renderHome(); };
  $("#rst").onclick = async () => { await post("/api/clock/reset", {}); await refreshClock(); await boot(); };
  $("#sed").onclick = async () => { await post("/api/seed", {}); await boot(); };
}
async function fillWho() {
  const kids = (await get("/api/children")).children || [];
  $("#who").innerHTML = kids.map((k) => `<option value="${k.child_id}">${esc(k.name)}（🔥${k.streak}）</option>`).join("") || '<option value="">（无）</option>';
  if (!state.cid || !kids.some((k) => k.child_id === state.cid)) state.cid = kids[0] ? kids[0].child_id : null;
  if (state.cid) $("#who").value = state.cid;
}
async function boot() { await refreshClock(); await fillWho(); await loadHome(); renderHome(); }
wire(); boot();
