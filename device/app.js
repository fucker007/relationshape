/* AI 最强大脑挑战机 · 设备端 App —— 纯 API 客户端，零业务逻辑。
   一套"真机使用感"的交互：AI 主动发起、一次一关、即时鼓励、峰终高光。*/
"use strict";
const api = async (m, p, b) => (await fetch(p, { method: m, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined })).json();
const get = (p) => api("GET", p), post = (p, b) => api("POST", p, b);
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const STAGE_EMOJI = ["🥚", "🐣", "🦎", "🐲", "🐉", "🌟"];
const ELEM_COLOR = { "晶": "#4aa3ff", "焰": "#ff6b5a", "声": "#b07bff", "风": "#46c98b", "光": "#ffc24a" };
const KIND_ICON = { warmup: "🧠", logic: "🧩", expression: "💬", observation: "👀", creation: "🎨" };
const RARITY_C = { "凡品": "#9aa3b8", "灵品": "#46c98b", "玄品": "#4aa3ff", "地品": "#b07bff", "天品": "#ffb454", "仙品": "#ff5a6e" };

const state = { cid: null, home: null, tab: "home", clock: null };
const view = () => $("#view"), screen = () => $(".screen");
const petEmoji = (p) => STAGE_EMOJI[Math.min(p.stage_index, 5)];
const setElem = (el) => screen().style.setProperty("--elem", ELEM_COLOR[el] || "#46c98b");

/* ---------- 通用动画 ---------- */
function confetti(n = 80) {
  const fx = $("#fx"), cols = ["#ff6b81", "#ffb12e", "#6c5ce7", "#46c98b", "#4aa3ff", "#ff8a5b"];
  for (let i = 0; i < n; i++) {
    const s = document.createElement("span");
    s.className = "confetti";
    s.style.left = Math.random() * 100 + "%"; s.style.top = "-20px";
    s.style.background = cols[i % cols.length];
    s.style.animationDuration = (1 + Math.random() * 1.2) + "s";
    s.style.animationDelay = (Math.random() * .3) + "s";
    s.style.transform = `rotate(${Math.random() * 360}deg)`;
    fx.appendChild(s); setTimeout(() => s.remove(), 2600);
  }
}
async function typewriter(node, text, sp = 26) {
  node.classList.add("caret"); node.textContent = "";
  for (let i = 0; i < text.length; i++) { node.textContent = text.slice(0, i + 1); await sleep(sp); }
  node.classList.remove("caret");
}
function ringSVG(pct) {
  const r = 104, c = 2 * Math.PI * r, off = c * (1 - Math.max(0, Math.min(100, pct)) / 100);
  return `<svg class="ring" viewBox="0 0 230 230"><circle cx="115" cy="115" r="${r}" fill="none" stroke="#ececf5" stroke-width="9"/>
    <circle cx="115" cy="115" r="${r}" fill="none" stroke="var(--elem)" stroke-width="9" stroke-linecap="round"
      stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/></svg>`;
}
function radarSVG(abils) {
  const cx = 120, cy = 108, R = 80, n = abils.length;
  const pt = (i, rr) => [cx + rr * Math.cos(-Math.PI / 2 + i * 2 * Math.PI / n), cy + rr * Math.sin(-Math.PI / 2 + i * 2 * Math.PI / n)];
  let g = "";
  for (const f of [0.34, 0.67, 1]) g += `<polygon points="${abils.map((_, i) => pt(i, R * f).map((v) => v.toFixed(1)).join(",")).join(" ")}" fill="none" stroke="#e9e7f5"/>`;
  for (let i = 0; i < n; i++) { const [x, y] = pt(i, R); g += `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#e9e7f5"/>`; }
  const poly = abils.map((a, i) => pt(i, R * Math.max(0.06, a.level / 100)).map((v) => v.toFixed(1)).join(",")).join(" ");
  g += `<polygon points="${poly}" fill="color-mix(in srgb,var(--brand) 26%,transparent)" stroke="var(--brand)" stroke-width="2"/>`;
  abils.forEach((a, i) => { const [x, y] = pt(i, R + 16); g += `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" font-size="11" fill="#8b8fa6" text-anchor="middle" dominant-baseline="middle">${esc(a.ability_zh)}</text>`; });
  return `<svg width="240" height="232" viewBox="0 0 240 232">${g}</svg>`;
}
const miniCard = (c) => `<span class="minicard flipin" style="--rc:${c.color || RARITY_C[c.rarity_zh] || "#ccc"}"><span class="mc-r">${esc(c.rarity_zh)}</span><span class="mc-n">${esc(c.name)}</span></span>`;

/* ---------- 数据 ---------- */
async function loadHome() {
  if (!state.cid) { state.home = null; return; }
  const h = await get(`/api/child/${state.cid}/home`);
  state.home = h.error ? null : h;
  if (state.home) setElem(state.home.pet.element);
}
async function refreshClock() { state.clock = await get("/api/clock"); }

/* ---------- 路由 ---------- */
function render(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  if (tab === "challenge") { startChallenge(); render("home"); return; }
  const v = view(); v.classList.remove("slide"); void v.offsetWidth; v.classList.add("slide");
  if (!state.cid) { v.innerHTML = `<div style="text-align:center;padding:60px 20px;color:var(--muted)">还没有设备数据。<br>点右侧「注入演示」。</div>`; return; }
  if (tab === "home") renderHome();
  else if (tab === "cards") renderCards();
  else if (tab === "me") renderMe();
}

/* ---------- 首页 ---------- */
function renderHome() {
  const h = state.home, p = h.pet, t = h.today;
  const hour = 9;
  const greetWord = t.all_done
    ? `今天 5 关全通关，太厉害了！明天我再来找你挑战 💪`
    : (t.done > 0 ? `已经闯了 ${t.done} 关啦，剩下 ${t.total - t.done} 关，继续冲！`
      : `早上好，<b>${esc(h.name)}</b>！今天的最强大脑挑战，准备好了吗？`);
  const ctaHTML = t.all_done
    ? `<button class="cta done" data-act="me">今日已完成 ✅<sub>去看看今天的成长</sub></button>`
    : `<button class="cta" data-act="go">${t.done > 0 ? "继续今天的挑战" : "开始今天的挑战"}<sub>${t.total - t.done} 关 · 约 ${t.total - t.done} 分钟</sub></button>`;
  view().innerHTML = `
    <div class="greet"><div class="coach">🤖</div><div class="greet-bubble">${greetWord}</div></div>
    <div class="hero">
      <div class="ringwrap">${ringSVG(t.total ? 100 * t.done / t.total : 0)}
        <div class="petorb"><div class="petemoji ${h.combat.form === "hot" ? "hot" : ""}">${petEmoji(p)}</div></div>
        <div class="ringpct">今日 ${t.done}/${t.total}</div>
      </div>
      <div class="hero-name">${esc(p.stage_name)}${h.combat.form === "hot" ? '<span class="formtag">🔥 火热</span>' : ""}</div>
      <div class="hero-realm">${esc(p.dominant_zh)}系 · ${esc(h.abilities.find((a) => a.ability === p.dominant)?.realm || "")}</div>
    </div>
    <div class="pods">
      <div class="pod"><b>${h.streak}</b><span>🔥 连续天</span></div>
      <div class="pod"><b>${h.power}</b><span>⚔️ 战力</span></div>
      <div class="pod"><b>${h.stars}</b><span>⭐ 智慧星</span></div>
    </div>
    ${ctaHTML}
    <button class="bump-cta" data-act="bump">⚡ 碰一碰 · 和好友的宠物比一比</button>
    <div class="quickchips">
      <span class="qchip">🏆 段位 <b>${esc(h.rank)}</b></span>
      <span class="qchip">🃏 卡册 <b>${h.cards.owned}/${h.cards.total}</b></span>
      <span class="qchip">📅 ${esc((state.clock && state.clock.day) || "")}</span>
    </div>`;
}

/* ---------- 挑战流程 ---------- */
let flow = null;
function startChallenge() {
  const h = state.home; if (!h) return;
  const steps = h.today.challenges.filter((c) => !c.answered);
  if (!steps.length) { renderCelebration(true); return; }
  flow = { steps, i: 0, stars: 0, growth: 0, cards: [], startPower: h.combat.battle_power };
  const node = document.createElement("div");
  node.className = "chal"; node.id = "chal"; screen().appendChild(node);
  renderStep();
}
function dotsHTML() {
  const total = state.home.today.total, doneBase = total - flow.steps.length;
  return Array.from({ length: total }, (_, k) => {
    const cls = k < doneBase + flow.i ? "done" : (k === doneBase + flow.i ? "active" : "");
    return `<span class="dot ${cls}"></span>`;
  }).join("");
}
async function renderStep() {
  const c = flow.steps[flow.i], node = $("#chal");
  node.innerHTML = `
    <div class="chal-top"><button class="chal-close">✕</button><div class="dots">${dotsHTML()}</div><div style="width:34px"></div></div>
    <div class="chal-kind"><div class="kind-ic">${KIND_ICON[c.kind] || "❓"}</div>
      <div><div class="kind-name">${esc(c.kind_zh)}</div><div class="kind-sub">${esc(c.ability_zh)} · 难度 ${"★".repeat(c.difficulty)}</div></div></div>
    <div class="coach-row"><div class="coach">🤖</div><div class="prompt" id="prompt"></div></div>
    <div class="answer">
      <textarea id="ans" placeholder="${c.score_mode === "effort" ? "把你想到的说出来…（认真说就有奖励，不打分对错）" : "写下你的答案…"}"></textarea>
      <div class="answer-row"><button class="mic" title="真机上是「说出来」">🎤</button>
        <button class="btn-primary" id="go" disabled>提交答案</button></div>
    </div>`;
  $(".chal-close").onclick = () => closeChallenge();
  const ta = $("#ans"), go = $("#go");
  ta.addEventListener("input", () => { go.disabled = !ta.value.trim(); });
  go.onclick = () => submitStep();
  await typewriter($("#prompt"), c.prompt, 22);
}
async function submitStep() {
  const c = flow.steps[flow.i], val = $("#ans").value.trim();
  $("#go").disabled = true;
  const r = await post(`/api/child/${state.cid}/answer`, { cid: c.cid, answer: val });
  if (r.error) { alert(r.detail || r.error); return; }
  const o = r.outcome; state.home = r.home; setElem(r.home.pet.element);
  flow.stars += o.stars_earned; flow.growth += o.growth_earned;
  (o.new_cards || []).forEach((x) => flow.cards.push(x));
  showFeedback(o, r.events || []);
}
function showFeedback(o, events) {
  const correct = o.correct, kind = correct === true ? "ok" : (correct === false ? "no" : "eff");
  const emoji = correct === true ? "🎉" : (correct === false ? "🤔" : "💛");
  const title = correct === true ? "答对啦！" : (correct === false ? "再想想～没关系" : "说得真好！");
  if (correct === true) confetti(70);
  const showExplain = o.explain && correct !== true;
  const fb = document.createElement("div");
  fb.className = "fb-card";
  fb.innerHTML = `
    <div class="fb-emoji">${emoji}</div>
    <div class="fb-title ${kind}">${title}</div>
    <div class="fb-box">${esc(o.feedback)}</div>
    ${showExplain ? `<div class="fb-box"><span class="lab">📘 讲解：</span>${esc(o.explain)}</div>` : ""}
    ${o.extend ? `<div class="fb-box"><span class="lab">💡 再想一步：</span>${esc(o.extend)}</div>` : ""}
    <div class="fb-reward"><span class="r">+${o.stars_earned} ⭐</span><span class="r g">+${o.growth_earned} 成长</span></div>
    ${(o.new_cards || []).length ? `<div class="fb-cards-row">${o.new_cards.map(miniCard).join("")}</div>` : ""}
    <button class="fb-next">${flow.i + 1 < flow.steps.length ? "下一关 →" : "完成今天的挑战 ✨"}</button>`;
  $("#chal").appendChild(fb);
  $(".fb-next").onclick = () => { fb.remove(); flow.i++; if (flow.i < flow.steps.length) renderStep(); else renderCelebration(false); };
}
function closeChallenge() { const n = $("#chal"); if (n) n.remove(); flow = null; loadHome().then(() => render("home")); }

function renderCelebration(already) {
  const h = state.home, p = h.pet;
  let node = $("#chal"); if (!node) { node = document.createElement("div"); node.className = "chal"; node.id = "chal"; screen().appendChild(node); }
  const dPower = flow ? h.combat.battle_power - flow.startPower : 0;
  const cards = flow ? flow.cards : [];
  node.innerHTML = `
    <div class="cele">
      <div class="cele-pet">${petEmoji(p)}</div>
      <div class="cele-title">${already ? "今天已经完成啦！" : "🎉 今日挑战完成！"}</div>
      <div class="cele-sub">${already ? "明天再来，连续天数会继续增长" : `连续坚持 ${h.streak} 天 · ${esc(p.stage_name)}`}</div>
      <div class="tally">
        <div class="tally-row"><span>⭐ 今日智慧星</span><b>+${flow ? flow.stars : 0}</b></div>
        <div class="tally-row"><span>🌱 宠物成长值</span><b>+${flow ? flow.growth : 0}</b></div>
        <div class="tally-row"><span>🔥 连续天数</span><b>${h.streak} 天</b></div>
        <div class="tally-row"><span>⚔️ 战力</span><b>${h.power}${dPower > 0 ? ` (+${dPower})` : ""}</b></div>
      </div>
      ${cards.length ? `<div class="section-t" style="margin-top:16px">🃏 获得新卡牌</div><div class="newcards">${cards.map(miniCard).join("")}</div>` : ""}
      <button class="cele-btn">回到首页</button>
    </div>`;
  if (!already) { confetti(120); }
  $(".cele-btn").onclick = () => closeChallenge();
}

/* ---------- 卡册 ---------- */
let albumDomain = null;
async function renderCards() {
  const d = await get(`/api/child/${state.cid}/album`);
  const groups = {};
  d.cards.forEach((c) => (groups[c.domain_zh] = groups[c.domain_zh] || []).push(c));
  const doms = Object.keys(groups);
  if (!albumDomain || !doms.includes(albumDomain)) albumDomain = doms[0];
  const tabs = doms.map((dm) => `<button class="cv-tab ${dm === albumDomain ? "active" : ""}" data-dom="${esc(dm)}">${esc(dm)}</button>`).join("");
  const grid = (groups[albumDomain] || []).map((c) => c.owned
    ? `<div class="gcard" style="--rc:${c.color}"><div class="gr">${esc(c.rarity_zh)}</div><div class="gicon">🃏</div><div class="gn">${esc(c.name)}</div><div class="gf">${esc(c.flavor)}</div>${c.owned > 1 ? `<div class="gcount">×${c.owned}</div>` : ""}</div>`
    : `<div class="gcard locked"><div class="gr">${esc(c.rarity_zh)}</div><div class="gicon">🔒</div><div class="gn">？？？</div><div class="gf">未获得</div></div>`).join("");
  view().innerHTML = `
    <div class="page-h"><h2>🃏 卡册</h2><span class="sub">${d.summary.owned}/${d.summary.total} · 藏卡战力 +${d.summary.bonus}</span></div>
    <div class="cv-tabs">${tabs}</div><div class="cgrid">${grid}</div>`;
  document.querySelectorAll(".cv-tab").forEach((b) => b.onclick = () => { albumDomain = b.dataset.dom; renderCards(); });
}

/* ---------- 我的 / 成长 ---------- */
function renderMe() {
  const h = state.home, c = h.combat;
  const strongest = h.abilities.reduce((a, b) => b.level > a.level ? b : a, h.abilities[0]);
  const maxbd = Math.max(1, ...c.breakdown.map((x) => x.value));
  view().innerHTML = `
    <div class="page-h"><h2>🌟 我的成长</h2></div>
    <div class="me-hero"><div class="lab">当前最强修为</div>
      <div class="big">${esc(strongest.ability_zh)} · ${esc(strongest.realm)}</div>
      <div class="pw">⚔️ 战力 ${h.power}　🏆 ${esc(h.rank)}　🃏 ${h.cards.owned} 张</div></div>
    <div class="radarwrap">${radarSVG(h.abilities)}</div>
    <div class="abils">${h.abilities.map((a) => `
      <div class="abrow"><div class="an">${esc(a.ability_zh)}<i>${esc(a.realm)}</i></div>
        <div class="track"><i style="width:${a.level}%"></i></div>
        <div class="av">lv${a.level} · ${a.accuracy == null ? "练习" : a.accuracy + "%"}</div></div>`).join("")}</div>
    ${(h.badges || []).length ? `<div class="section-t">🏅 徽章墙</div><div class="badges2">${h.badges.map((b) => `<span class="bdg">🏅 ${esc(b)}</span>`).join("")}</div>` : ""}
    <div class="powerbox"><div class="pt"><span>⚔️ 战力构成</span><b>${h.power}</b></div>
      ${c.breakdown.map((x) => `<div class="bdbar"><span>${esc(x.label)}</span><div class="bt2"><i style="width:${Math.round(100 * x.value / maxbd)}%"></i></div><span>${x.value}</span></div>`).join("")}</div>
    <button class="parent-btn" data-act="parent">👪 家长报告</button>`;
}

async function showParent() {
  const rep = await get(`/api/child/${state.cid}/report`);
  const rows = rep.abilities.map((a) => `<tr><td>${esc(a.ability_zh)} <b style="color:var(--elem)">${esc(a.realm)}</b></td><td>${a.week_ago}</td><td>${a.now}</td><td class="${a.delta > 0 ? "up" : ""}">${a.delta > 0 ? "+" + a.delta : a.delta}</td><td>${a.accuracy == null ? "—" : a.accuracy + "%"}</td></tr>`).join("");
  const bd = rep.combat.breakdown.map((p) => `${esc(p.label)} ${p.value}`).join(" ＋ ");
  const hls = (rep.highlights || []).map((x) => `<div class="rep-hl"><div class="q">${esc(x.prompt)}</div>“${esc(x.answer)}”</div>`).join("") || '<span style="color:var(--muted);font-size:12px">本周还没有高光片段</span>';
  const sheet = document.createElement("div");
  sheet.className = "sheet";
  sheet.innerHTML = `<div class="sheet-card">
    <h3>👪 ${esc(rep.name)} · 本周成长报告</h3>
    <table class="rep-tab"><tr><th>能力·境界</th><th>周初</th><th>现在</th><th>变化</th><th>正确率</th></tr>${rows}</table>
    <div class="rep-pow">⚔️ 战力 <b>${rep.combat.power}</b>（${esc(rep.combat.rank)}）＝ ${bd}<br>都是孩子真实做出来的：体魄来自坚持、暴击来自正确率。</div>
    <div class="section-t" style="margin:0 0 8px">✨ 本周亮点</div>${hls}
    <div class="rep-note">${esc(rep.honest_note)}</div>
    <button class="sheet-close">收起</button></div>`;
  screen().appendChild(sheet);
  $(".sheet-close").onclick = () => sheet.remove();
  sheet.onclick = (e) => { if (e.target === sheet) sheet.remove(); };
}

/* ---------- 碰一碰对战 ---------- */
async function startBattle() {
  const kids = (await get("/api/children")).children || [];
  const others = kids.filter((k) => k.child_id !== state.cid);
  if (!others.length) { alert("需要另一台设备（右侧「注入演示」会多一个孩子）"); return; }
  const res = await post("/api/battle", { a: state.cid, b: others[0].child_id });
  if (res.error) { alert(res.detail || res.error); return; }
  await playBattle(res);
}
function fighterHTML(side, name, rank, stats) {
  return `<div class="bf" id="bf${side}"><div class="bn">${esc(name)}</div><div class="brk">${esc(rank)} · 战力${stats.battle_power}</div>
    <div class="bhp"><i id="bhp${side}" style="width:100%"></i></div>
    <div class="bpet" id="bpet${side}">${STAGE_EMOJI[Math.min(stats.stage_index || 3, 5)] || "🐉"}</div></div>`;
}
async function playBattle(res) {
  const node = document.createElement("div"); node.className = "bt"; node.id = "bt"; screen().appendChild(node);
  node.innerHTML = `
    <button class="bt-skip">跳过 »</button>
    <div class="bt-fighters">${fighterHTML("a", res.a_name, res.a_rank, res.a_stats)}<div class="bvs">VS</div>${fighterHTML("b", res.b_name, res.b_rank, res.b_stats)}</div>
    ${res.friendly ? '<div class="bt-friendly">🤝 段位悬殊 → 友谊赛：抹平差距、点到为止，输了不掉成长值</div>' : ""}
    <div id="bt-result"></div>`;
  let skip = false; $(".bt-skip").onclick = () => { skip = true; };
  for (const e of res.log) {
    if (skip) break;
    const atk = $("#bpet" + e.actor), foe = $("#bf" + e.foe);
    atk.classList.add("lunge-" + e.actor);
    $("#bhpa").style.width = e.pct_a + "%"; $("#bhpb").style.width = e.pct_b + "%";
    const d = document.createElement("div"); let lbl = "-" + e.dmg, cls = "dmg";
    if (e.move === "special") { lbl = "绝招! -" + e.dmg; cls += " special"; }
    if (e.crit) { lbl = "暴击! -" + e.dmg; cls += " crit"; node.classList.add("shake"); }
    if (e.adv > 0) lbl += " 克制";
    d.className = cls; d.textContent = lbl; d.style.left = (e.foe === "a" ? 22 : 64) + "%"; d.style.top = "44%";
    node.appendChild(d);
    setTimeout(() => { atk.classList.remove("lunge-" + e.actor); node.classList.remove("shake"); }, 260);
    setTimeout(() => d.remove(), 900);
    await sleep(skip ? 0 : 460);
  }
  const last = res.log[res.log.length - 1] || { pct_a: 100, pct_b: 100 };
  $("#bhpa").style.width = last.pct_a + "%"; $("#bhpb").style.width = last.pct_b + "%";
  const nc = [...(res.a_new_cards || [])];
  $("#bt-result").innerHTML = `<div class="bt-result">
    <div class="bnarr">${res.ko ? "💥 " : "🏁 "}${esc(res.narration)}</div>
    <div class="brew">你：+${res.a_reward.stars}⭐ +${res.a_reward.growth}成长${nc.length ? "　新卡 " + nc.map((c) => c.name).join("、") : ""}</div>
    <div class="bt-actions"><button class="again">再碰一碰</button><button class="close">收起</button></div></div>`;
  if (res.winner === "a" && !res.friendly) confetti(80);
  $(".again").onclick = () => { node.remove(); startBattle(); };
  $(".close").onclick = () => { node.remove(); loadHome().then(() => render(state.tab === "challenge" ? "home" : state.tab)); };
}

/* ---------- 事件 ---------- */
function wire() {
  $("#tabbar").addEventListener("click", (e) => { const t = e.target.closest(".tab"); if (t) render(t.dataset.tab); });
  view().addEventListener("click", (e) => {
    const a = e.target.closest("[data-act]"); if (!a) return;
    const act = a.dataset.act;
    if (act === "go") startChallenge();
    else if (act === "me") render("me");
    else if (act === "bump") startBattle();
    else if (act === "parent") showParent();
  });
  $("#who").addEventListener("change", async (e) => { state.cid = e.target.value || null; await loadHome(); render("home"); });
  $("#adv").onclick = async () => { await post("/api/clock/advance", { days: 1 }); await refreshClock(); await loadHome(); render(state.tab); };
  $("#rst").onclick = async () => { await post("/api/clock/reset", {}); await refreshClock(); await boot(); };
  $("#sed").onclick = async () => { await post("/api/seed", {}); await boot(); };
}
async function fillWho() {
  const kids = (await get("/api/children")).children || [];
  $("#who").innerHTML = kids.map((k) => `<option value="${k.child_id}">${esc(k.name)}（${esc(k.rank)}·🔥${k.streak}）</option>`).join("") || '<option value="">（无）</option>';
  if (!state.cid || !kids.some((k) => k.child_id === state.cid)) state.cid = kids[0] ? kids[0].child_id : null;
  if (state.cid) $("#who").value = state.cid;
}
async function boot() {
  await refreshClock(); await fillWho(); await loadHome(); render("home");
}
wire(); boot();
