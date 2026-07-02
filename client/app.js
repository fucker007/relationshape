/* AI 最强大脑挑战机 —— 纯客户端模拟器。
 *
 * 浏览器零业务逻辑：判分/养成/战斗胜负/战力/卡牌全在服务端。这里只渲染状态、发动作、
 * 播放服务端给的 battle_log 动画。服务端连答案都不下发。
 */
"use strict";

const api = async (m, p, b) => (await fetch(p, {
  method: m, headers: { "Content-Type": "application/json" },
  body: b ? JSON.stringify(b) : undefined,
})).json();
const get = (p) => api("GET", p);
const post = (p, b) => api("POST", p, b);
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ELEM_COLOR = { "晶": "#4aa3ff", "焰": "#ff6b5a", "声": "#b07bff", "风": "#46c98b", "光": "#ffc24a" };

const slots = {
  A: { cid: null, outcome: null, events: [], view: "device" },
  B: { cid: null, outcome: null, events: [], view: "device" },
};
const panel = (slot) => document.querySelector(`.device[data-slot="${slot}"]`);

/* ---------------- 小屏幕：宠物 + 成长轨道 ---------------- */
const MAT_ZH = { li: "晶尘", wen: "韵露", bo: "灵芝" };
function petScreen(h) {
  const p = h.pet;
  const isEgg = p.realm === "egg";
  const emoji = isEgg ? "🥚" : (p.species ? p.species.emoji : "🐲");
  const color = ELEM_COLOR[p.element] || "#5b8cff";
  const title = isEgg ? `神秘的蛋 · 孵化 ${p.realm_day}/${p.days_to_hatch} 天` : `${p.species.name} · ${p.realm_zh}`;
  const pct = isEgg ? Math.round(100 * p.realm_day / p.days_to_hatch)
    : Math.round(100 * h.today.done / Math.max(1, h.today.total));
  const mats = Object.entries(p.materials || {})
    .map(([d, n]) => `<span class="chip item">${esc(MAT_ZH[d] || d)} ×${n}</span>`).join("");
  return `
  <div class="screen" style="--elem:${color}">
    <div class="track">
      <div class="cell">🔥 连续<b>${h.streak}</b>天</div>
      <div class="cell">✅ 今日<b>${h.today.done}/${h.today.total}</b></div>
      <div class="cell">⭐ 星<b>${h.stars}</b></div>
    </div>
    <div class="petwrap">
      <div class="petemoji ${h.combat.form === "hot" ? "hot" : ""}">${emoji}</div>
      <div class="petname">${esc(title)} · <span class="elem">${esc(p.dominant_zh)}亲和（${esc(p.element)}系）</span>
        ${h.combat.form === "hot" ? '<span class="formtag">🔥状态火热</span>' : ""}</div>
    </div>
    <div class="stagebar"><i style="width:${pct}%"></i><span>${isEgg ? "孵化进度" : "今日修炼"} ${pct}%</span></div>
    <div class="vitwrap"><div class="vit"><i style="width:${p.vitality}%"></i></div></div>
    <div class="metaline">${esc(p.status_line)}</div>
    <div class="chips">${mats || '<span class="muted">还没有灵材——答题就是喂养</span>'}</div>
  </div>`;
}

/* 战力面板：总分 + 拆解(看清因果) + 战斗属性 */
function combatPanel(h) {
  const c = h.combat;
  const max = Math.max(1, ...c.breakdown.map((p) => p.value));
  const bars = c.breakdown.map((p) => `
    <div class="bd"><span class="bdl">${esc(p.label)}<i>${esc(p.source)}</i></span>
      <div class="bar"><i style="width:${Math.round(100 * p.value / max)}%"></i></div>
      <span class="bdv">${p.value}</span></div>`).join("");
  return `
  <div class="combat">
    <div class="combat-top"><span>⚔️ 战力 <b>${c.battle_power}</b></span>
      <span class="rankpill">🏆 ${esc(h.pet.realm_zh)}</span></div>
    <div class="cstats">
      <span title="努力(坚持/活跃)">❤️HP ${c.hp}</span>
      <span title="逻辑">🗡ATK ${c.atk}</span>
      <span title="正确率">🎯暴击 ${Math.round(c.crit * 100)}%</span>
      <span title="专注">💨SPD ${c.spd}</span>
      <span title="表达">✨绝招 ×${c.special}</span>
    </div>
    <div class="breakdown">${bars}</div>
  </div>`;
}

function abilitiesBlock(h) {
  return `<div class="abilities">${h.abilities.map((a) => {
    const acc = a.accuracy == null ? "练习" : a.accuracy + "%";
    return `<div class="ability">
      <span class="abn">${esc(a.ability_zh)}<i class="realm">${esc(a.mastery)}</i></span>
      <div class="bar"><i style="width:${a.level}%"></i></div>
      <span class="abval">${a.level}<i>${acc}</i></span></div>`;
  }).join("")}</div>`;
}

function cardsBar(h) {
  const c = h.cards;
  return `<div class="cardsbar" data-album="1">
    🃏 卡册 <b>${c.owned}/${c.total}</b>　藏卡战力 +${c.bonus}
    <span class="more">点开看卡册 ▸</span></div>`;
}

function challengeCard(c, i) {
  if (c.answered) {
    const r = c.result || {};
    const cls = r.correct === true ? "ok" : (r.correct === false ? "no" : "eff");
    const tag = r.correct === true ? "✅ 答对了" : (r.correct === false ? "🔁 再接再厉（已记努力）" : "💬 已认真参与");
    return `<div class="ccard done"><div class="ctop"><span class="ckind">${i + 1}. ${esc(c.kind_zh)}</span><span class="cab">${esc(c.ability_zh)}·难度${c.difficulty}</span></div>
      <div class="cprompt">${esc(c.prompt)}</div><div class="cresult ${cls}">${tag}</div></div>`;
  }
  const ph = c.score_mode === "effort" ? "说说你的想法…（不判对错，认真说就有奖励）" : "输入你的答案…";
  return `<div class="ccard"><div class="ctop"><span class="ckind">${i + 1}. ${esc(c.kind_zh)}</span><span class="cab">${esc(c.ability_zh)}·难度${c.difficulty}</span></div>
    <div class="cprompt">${esc(c.prompt)}</div>
    <div class="canswer"><input data-cid="${c.cid}" placeholder="${ph}"><button class="sub" data-cid="${c.cid}">提交</button></div></div>`;
}

function feedbackBlock(slot) {
  const o = slots[slot].outcome, evs = slots[slot].events || [];
  let html = "";
  if (o) {
    const showExplain = o.explain && (o.correct === false || o.correct === null);
    const cards = (o.new_cards || []).map((c) =>
      `<span class="minicard" style="border-color:${c.color}">🃏 ${esc(c.name)}·${esc(c.rarity_zh)}</span>`).join("");
    html += `<div class="feedback"><div class="fb-main">${esc(o.feedback)}</div>
      ${showExplain ? `<div class="fb-explain">📘 讲解：${esc(o.explain)}</div>` : ""}
      ${o.extend ? `<div class="fb-extend">💡 再想一步：${esc(o.extend)}</div>` : ""}
      <div class="fb-reward">+${o.stars_earned}⭐${o.fed ? `　+1颗${esc(o.fed.material)}` : ""}${o.is_highlight ? "　🌟 高光" : ""}</div>
      ${cards ? `<div class="fb-cards">${cards}</div>` : ""}</div>`;
  }
  if (evs.length) html += `<div class="eventfeed">${evs.map((e) =>
    `<div class="ev ${e.kind}">${e.kind === "realm" ? "🌀" : (e.kind === "card" ? "🃏" : "🎉")} ${esc(e.label)}</div>`).join("")}</div>`;
  return html;
}

function renderHome(slot, h) {
  slots[slot].home = h;
  const t = h.today;
  panel(slot).querySelector(".device-content").innerHTML =
    petScreen(h) + combatPanel(h) + abilitiesBlock(h) + cardsBar(h) +
    `<div class="challenges"><h4>今日挑战 ${t.done}/${t.total}${t.all_done ? " 🎉 全部完成！" : ""}</h4>
       ${t.challenges.map((c, i) => challengeCard(c, i)).join("")}</div>` + feedbackBlock(slot);
}

/* ---------------- 动作 ---------------- */
async function loadSlot(slot) {
  const cid = slots[slot].cid;
  const content = panel(slot).querySelector(".device-content");
  if (!cid) { content.innerHTML = '<div class="empty">这台设备还没绑定孩子。<br>点顶部「注入演示数据」，或「＋设备」新建。</div>'; return; }
  const h = await get(`/api/child/${cid}/home`);
  if (h.error) { content.innerHTML = `<div class="empty">加载失败：${esc(h.detail || h.error)}</div>`; return; }
  renderHome(slot, h);
}

async function submitAnswer(slot, cid, value) {
  const r = await post(`/api/child/${slots[slot].cid}/answer`, { cid, answer: value });
  if (r.error) { alert("出错了：" + (r.detail || r.error)); return; }
  slots[slot].outcome = r.outcome || null;
  slots[slot].events = r.events || [];
  renderHome(slot, r.home);
}

/* ---------------- 家长后台 ---------------- */
async function toggleReport(slot) {
  const p = panel(slot), rep = p.querySelector(".report-panel"), content = p.querySelector(".device-content");
  if (slots[slot].view === "report") {
    slots[slot].view = "device"; rep.classList.add("hidden"); content.classList.remove("hidden");
    p.querySelector(".view-toggle").textContent = "家长后台"; return;
  }
  if (!slots[slot].cid) { alert("先绑定一个孩子"); return; }
  rep.innerHTML = reportView(await get(`/api/child/${slots[slot].cid}/report`));
  slots[slot].view = "report"; rep.classList.remove("hidden"); content.classList.add("hidden");
  p.querySelector(".view-toggle").textContent = "返回设备";
}

function reportView(rep) {
  const w = rep.this_week;
  const rows = rep.abilities.map((a) => {
    const acc = a.accuracy == null ? "—（练习量）" : a.accuracy + "%";
    return `<tr><td>${esc(a.ability_zh)}<i class="rm">${esc(a.mastery)}</i></td><td>${a.week_ago}</td><td>${a.now}</td>
      <td class="delta ${a.delta > 0 ? "up" : "flat"}">${a.delta > 0 ? "+" + a.delta : a.delta}</td><td>${a.practiced}</td><td>${acc}</td></tr>`;
  }).join("");
  const bd = rep.combat.breakdown.map((p) => `${esc(p.label)} ${p.value}<i>(${esc(p.source)})</i>`).join(" ＋ ");
  const hls = (rep.highlights || []).map((h) => `<div class="rep-hl"><div class="q">${esc(h.prompt)}</div>“${esc(h.answer)}”</div>`).join("")
    || '<span class="muted">本周还没有高光片段。</span>';
  const imp = rep.biggest_improvement ? `${esc(rep.biggest_improvement.ability_zh)} +${rep.biggest_improvement.delta}` : "—";
  return `<h3>👪 ${esc(rep.name)} · 本周成长报告</h3>
    <table class="rep-abil"><tr><th>能力·修为</th><th>周初</th><th>现在</th><th>变化</th><th>练习</th><th>正确率</th></tr>${rows}</table>
    <div class="rep-power">⚔️ 战力 <b>${rep.combat.power}</b>（${esc(rep.combat.realm_zh)}）＝ ${bd}</div>
    <div class="rep-week">
      <div class="stat"><b>${w.completed_days}</b>完成天数</div><div class="stat"><b>${w.challenges_done}</b>挑战数</div>
      <div class="stat"><b>${w.streak}</b>连续</div><div class="stat"><b>${rep.cards.owned}</b>卡牌</div><div class="stat"><b>${w.stars}</b>星</div></div>
    <div style="font-size:13px;margin-bottom:8px">📈 最大进步：<b>${imp}</b></div>
    <div style="font-weight:700;font-size:13px;margin:6px 0">✨ 本周亮点（最真实的几句）</div>${hls}
    <div class="rep-note">${esc(rep.honest_note)}</div>`;
}

/* ---------------- 卡册 ---------------- */
async function showAlbum(slot) {
  if (!slots[slot].cid) { alert("先绑定一个孩子"); return; }
  const d = await get(`/api/child/${slots[slot].cid}/album`);
  const groups = {};
  d.cards.forEach((c) => { (groups[c.domain_zh] = groups[c.domain_zh] || []).push(c); });
  const sec = Object.entries(groups).map(([dom, cs]) => `
    <div class="alb-sec"><h4>${esc(dom)}</h4><div class="alb-grid">${cs.map((c) => c.owned
      ? `<div class="acard" style="--rc:${c.color}"><div class="ar">${esc(c.rarity_zh)}</div><div class="an">${esc(c.name)}</div><div class="af">${esc(c.flavor)}</div>${c.owned > 1 ? `<div class="acount">×${c.owned}</div>` : ""}</div>`
      : `<div class="acard locked"><div class="ar">${esc(c.rarity_zh)}</div><div class="an">？？？</div><div class="af">未获得</div></div>`).join("")}</div></div>`).join("");
  $("#modal-card").innerHTML = `
    <div class="alb-head"><h3>🃏 卡册 <span class="muted">${d.summary.owned}/${d.summary.total} · 藏卡战力 +${d.summary.bonus}</span></h3>
      <button id="alb-close" class="ghost">关闭</button></div>
    <div class="alb-body">${sec}</div>`;
  $("#modal").classList.remove("hidden");
  $("#alb-close").onclick = () => $("#modal").classList.add("hidden");
}

/* ---------------- 碰一碰：播放 battle_log 的战斗画面 ---------------- */
async function bump() {
  if (!slots.A.cid || !slots.B.cid) { alert("需要两台设备各绑定一个孩子"); return; }
  if (slots.A.cid === slots.B.cid) { alert("两台设备要选不同的孩子"); return; }
  const res = await post("/api/battle", { a: slots.A.cid, b: slots.B.cid });
  if (res.error) { alert("对战失败：" + (res.detail || res.error)); return; }
  await playBattle(res);
  renderHome("A", res.a_home); renderHome("B", res.b_home);
}

function fighterHTML(side, name, rank, emoji, stats) {
  return `<div class="fighter" id="f${side}" style="--elem:${ELEM_COLOR[stats.element] || "#888"}">
    <div class="fname">${esc(name)} <span class="frank">${esc(rank)}</span></div>
    <div class="hpbar"><i id="hp${side}" style="width:100%"></i></div>
    <div class="fpet" id="pet${side}">${stats.form === "hot" ? "🔥" : ""}${emoji}</div>
    <div class="ftags">${esc(stats.element)}系 · 战力${stats.battle_power}${stats.form === "hot" ? " · 火热" : ""}</div>
  </div>`;
}

async function playBattle(res) {
  const card = $("#modal-card");
  card.innerHTML = `
    <div class="arena">
      ${fighterHTML("a", res.a_name, res.realm_zh, res.a_emoji, res.a_stats)}
      <div class="arena-mid"><div class="vsflash">VS</div><button id="bt-skip" class="ghost">跳过 »</button></div>
      ${fighterHTML("b", res.b_name, res.realm_zh, res.b_emoji, res.b_stats)}
    </div>
    ${res.friendly ? '<div class="bt-friendly">🤝 实力悬殊 → 友谊赛：抹平差距、点到为止，输了不掉成长</div>' : ""}
    <div id="bt-result" class="hidden"></div>`;
  $("#modal").classList.remove("hidden");

  let skipped = false;
  $("#bt-skip").onclick = () => { skipped = true; };
  const arena = $(".arena");

  for (const e of res.log) {
    if (skipped) break;
    const atk = $("#pet" + e.actor), foe = $("#f" + e.foe);
    atk.classList.add("lunge-" + e.actor);
    $("#hpa").style.width = e.pct_a + "%";
    $("#hpb").style.width = e.pct_b + "%";
    // 飘字
    const d = document.createElement("div");
    let lbl = "-" + e.dmg, cls = "dmg";
    if (e.move === "special") { lbl = "绝招! -" + e.dmg; cls += " special"; }
    if (e.crit) { lbl = "暴击! -" + e.dmg; cls += " crit"; arena.classList.add("shake"); }
    if (e.adv > 0) lbl += " 克制";
    if (e.twist) lbl += " 变招";
    d.className = cls; d.textContent = lbl;
    foe.appendChild(d);
    setTimeout(() => { atk.classList.remove("lunge-" + e.actor); arena.classList.remove("shake"); }, 280);
    setTimeout(() => d.remove(), 850);
    await sleep(skipped ? 0 : 470);
  }
  // 收尾：把血量补到最终
  const last = res.log[res.log.length - 1] || { pct_a: 100, pct_b: 100 };
  $("#hpa").style.width = last.pct_a + "%"; $("#hpb").style.width = last.pct_b + "%";
  const wname = res.winner === "a" ? res.a_name : res.b_name;
  const newCards = [...(res.a_new_cards || []), ...(res.b_new_cards || [])];
  $("#bt-result").innerHTML = `
    <div class="bt-narr">${res.ko ? "💥 " : "🏁 "}${esc(res.narration)}</div>
    <div class="bt-reward"><span>${esc(res.a_name)}：+${res.a_reward.stars}⭐ +${res.a_reward.material.n}颗${esc(res.a_reward.material.name)}</span>
      <span>${esc(res.b_name)}：+${res.b_reward.stars}⭐ +${res.b_reward.material.n}颗${esc(res.b_reward.material.name)}</span></div>
    ${newCards.length ? `<div class="bt-cards">新卡：${newCards.map((c) => `<span class="minicard" style="border-color:${c.color}">🃏${esc(c.name)}</span>`).join("")}</div>` : ""}
    <div class="bt-actions"><button id="bt-again">再碰一碰</button><button id="bt-close" class="ghost">关闭</button></div>`;
  $("#bt-result").classList.remove("hidden");
  $("#bt-again").onclick = () => { $("#modal").classList.add("hidden"); bump(); };
  $("#bt-close").onclick = () => $("#modal").classList.add("hidden");
}

/* ---------------- 选择器 / 顶栏 ---------------- */
async function refreshSelectors(keep = true) {
  const data = await get("/api/children");
  const kids = data.children || [];
  const has = (id) => id && kids.some((k) => k.child_id === id);
  const want = {
    A: (keep && has(slots.A.cid)) ? slots.A.cid : (kids[0] && kids[0].child_id) || "",
    B: (keep && has(slots.B.cid)) ? slots.B.cid : ((kids[1] || kids[0] || {}).child_id) || "",
  };
  for (const slot of ["A", "B"]) {
    const sel = panel(slot).querySelector(".child-select");
    sel.innerHTML = kids.length
      ? kids.map((k) => `<option value="${k.child_id}">${esc(k.name)}（${esc(k.realm_zh)}·战力${k.power}·🔥${k.streak}·🃏${k.cards}）</option>`).join("")
      : '<option value="">（暂无设备）</option>';
    sel.value = want[slot]; slots[slot].cid = want[slot] || null;
  }
  if (data.day) $("#clock").textContent = "📅 " + data.day;
}
async function refreshClock() { const c = await get("/api/clock"); $("#clock").textContent = "📅 " + c.day; }

async function newChild(slot) {
  const name = prompt("孩子的名字？", "小朋友");
  if (name === null) return;
  const grade = parseInt(prompt("几年级？(1-3)", "2") || "2", 10);
  const r = await post("/api/children", { name, grade, age: 5 + grade });
  if (r.error) { alert("创建失败：" + (r.detail || r.error)); return; }
  await refreshSelectors(); slots[slot].cid = r.child_id;
  panel(slot).querySelector(".child-select").value = r.child_id; await loadSlot(slot);
}

function wire() {
  document.body.addEventListener("click", (e) => {
    const sub = e.target.closest(".sub");
    if (sub) { const slot = sub.closest(".device").dataset.slot; submitAnswer(slot, sub.dataset.cid, sub.parentElement.querySelector("input").value.trim()); return; }
    if (e.target.closest(".newchild")) { newChild(e.target.closest(".device").dataset.slot); return; }
    if (e.target.closest(".view-toggle")) { toggleReport(e.target.closest(".device").dataset.slot); return; }
    if (e.target.closest(".album-btn")) { showAlbum(e.target.closest(".device").dataset.slot); return; }
    if (e.target.closest(".cardsbar")) { showAlbum(e.target.closest(".device").dataset.slot); return; }
  });
  document.body.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.matches(".canswer input")) {
      const card = e.target.closest(".device");
      submitAnswer(card.dataset.slot, e.target.dataset.cid, e.target.value.trim());
    }
  });
  for (const slot of ["A", "B"]) {
    panel(slot).querySelector(".child-select").addEventListener("change", (e) => {
      slots[slot].cid = e.target.value || null; slots[slot].outcome = null; slots[slot].events = [];
      if (slots[slot].view === "report") toggleReport(slot);
      loadSlot(slot);
    });
  }
  $("#bump").onclick = bump;
  $("#advance").onclick = async () => { await post("/api/clock/advance", { days: 1 }); await refreshClock(); await refreshSelectors(); await loadSlot("A"); await loadSlot("B"); };
  $("#reset").onclick = async () => { await post("/api/clock/reset", {}); await refreshClock(); await refreshSelectors(); await loadSlot("A"); await loadSlot("B"); };
  $("#seed").onclick = async () => { await post("/api/seed", {}); await refreshSelectors(false); await loadSlot("A"); await loadSlot("B"); };
}

async function boot() {
  wire(); await refreshClock(); await refreshSelectors(); await loadSlot("A"); await loadSlot("B");
}
boot();
