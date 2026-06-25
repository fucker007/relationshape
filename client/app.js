/* AI 最强大脑挑战机 —— 纯客户端模拟器。
 *
 * 设计约束：浏览器里没有任何业务逻辑。判分、能力评估、养成、战力、对战胜负、
 * 报告——全部来自服务端 JSON。这里只做两件事：把状态画出来、把用户动作发出去。
 * 服务端连"正确答案"都不下发，客户端想作弊也无从下手。
 */
"use strict";

const api = async (method, path, body) => {
  const r = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
};
const get = (p) => api("GET", p);
const post = (p, b) => api("POST", p, b);
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const STAGE_EMOJI = ["🥚", "🐣", "🦎", "🐲", "🐉", "🌟"];
const ELEM_COLOR = { "晶": "#4aa3ff", "焰": "#ff6b5a", "声": "#b07bff", "风": "#46c98b", "光": "#ffc24a" };

const slots = {
  A: { cid: null, home: null, outcome: null, events: [], view: "device" },
  B: { cid: null, home: null, outcome: null, events: [], view: "device" },
};
const panel = (slot) => document.querySelector(`.device[data-slot="${slot}"]`);

/* ---------------- 渲染：小屏幕（宠物 + 成长轨道）---------------- */
function petScreen(home) {
  const p = home.pet;
  const emoji = STAGE_EMOJI[Math.min(p.stage_index, STAGE_EMOJI.length - 1)];
  const color = ELEM_COLOR[p.element] || "#5b8cff";
  const items = Object.values(p.equipped || {});
  const stageTxt = p.next_stage
    ? `${p.stage_name} → ${p.next_stage} ${p.stage_pct}%`
    : `${p.stage_name}（满级）`;
  return `
  <div class="screen" style="--elem:${color}">
    <div class="track">
      <div class="cell">🔥 连续<b>${home.streak}</b>天</div>
      <div class="cell">✅ 今日<b>${home.today.done}/${home.today.total}</b></div>
    </div>
    <div class="petwrap">
      <div class="petemoji">${emoji}</div>
      <div class="petname">${esc(p.stage_name)} · <span class="elem">${esc(p.dominant_zh)}（${esc(p.element)}系）</span></div>
    </div>
    <div class="stagebar"><i style="width:${p.stage_pct}%"></i><span>${esc(stageTxt)}</span></div>
    <div class="vitwrap"><div class="vit"><i style="width:${p.vitality}%"></i></div></div>
    <div class="track" style="margin-top:8px">
      <div class="cell">⚔️ 战力<b>${home.power}</b></div>
      <div class="cell">⭐ 智慧星<b>${home.stars}</b></div>
      <div class="cell">🏆 段位<b style="font-size:13px">${esc(home.rank)}</b></div>
    </div>
    <div class="metaline">活力 <b>${p.vitality}</b>/100（每天会落一点，做挑战回补——它需要你照顾）</div>
    <div class="chips">${items.length
      ? items.map((i) => `<span class="chip item">🎽 ${esc(i)}</span>`).join("")
      : '<span class="muted">还没有装扮，坚持挑战 / 得徽章可解锁</span>'}</div>
    <div class="chips">${(home.badges || []).length
      ? home.badges.map((b) => `<span class="chip badge">🏅${esc(b)}</span>`).join("")
      : '<span class="muted">徽章墙空空的，去赢一枚吧</span>'}</div>
  </div>`;
}

function abilitiesBlock(home) {
  return `<div class="abilities">${home.abilities.map((a) => `
    <div class="ability">
      <span>${esc(a.ability_zh)}</span>
      <div class="bar"><i style="width:${a.level}%"></i></div>
      <span class="abval">${a.level}</span>
    </div>`).join("")}</div>`;
}

function challengeCard(c, i) {
  if (c.answered) {
    const r = c.result || {};
    const cls = r.correct === true ? "ok" : (r.correct === false ? "no" : "eff");
    const tag = r.correct === true ? "✅ 答对了" : (r.correct === false ? "🔁 再接再厉（已记努力）" : "💬 已认真参与");
    return `<div class="ccard done">
      <div class="ctop"><span class="ckind">${i + 1}. ${esc(c.kind_zh)}</span><span class="cab">${esc(c.ability_zh)}·难度${c.difficulty}</span></div>
      <div class="cprompt">${esc(c.prompt)}</div>
      <div class="cresult ${cls}">${tag}</div>
    </div>`;
  }
  const ph = c.score_mode === "effort" ? "说说你的想法…（不判对错，认真说就有奖励）" : "输入你的答案…";
  return `<div class="ccard">
    <div class="ctop"><span class="ckind">${i + 1}. ${esc(c.kind_zh)}</span><span class="cab">${esc(c.ability_zh)}·难度${c.difficulty}</span></div>
    <div class="cprompt">${esc(c.prompt)}</div>
    <div class="canswer">
      <input data-cid="${c.cid}" placeholder="${ph}">
      <button class="sub" data-cid="${c.cid}">提交</button>
    </div>
  </div>`;
}

function feedbackBlock(slot) {
  const o = slots[slot].outcome;
  const evs = slots[slot].events || [];
  let html = "";
  if (o) {
    const showExplain = o.explain && (o.correct === false || o.correct === null);
    html += `<div class="feedback">
      <div class="fb-main">${esc(o.feedback)}</div>
      ${showExplain ? `<div class="fb-explain">📘 讲解：${esc(o.explain)}</div>` : ""}
      ${o.extend ? `<div class="fb-extend">💡 再想一步：${esc(o.extend)}</div>` : ""}
      <div class="fb-reward">+${o.stars_earned}⭐　+${o.growth_earned} 成长值${o.is_highlight ? "　🌟 被存为高光（进家长周报）" : ""}</div>
    </div>`;
  }
  if (evs.length) {
    html += `<div class="eventfeed">${evs.map((e) => `<div class="ev">🎉 ${esc(e.label)}</div>`).join("")}</div>`;
  }
  return html;
}

function renderHome(slot, home) {
  slots[slot].home = home;
  const t = home.today;
  const content = panel(slot).querySelector(".device-content");
  content.innerHTML =
    petScreen(home) +
    abilitiesBlock(home) +
    `<div class="challenges"><h4>今日挑战 ${t.done}/${t.total}${t.all_done ? " 🎉 全部完成！" : ""}</h4>
       ${t.challenges.map((c, i) => challengeCard(c, i)).join("")}</div>` +
    feedbackBlock(slot);
}

/* ---------------- 动作 ---------------- */
async function loadSlot(slot) {
  const cid = slots[slot].cid;
  const content = panel(slot).querySelector(".device-content");
  if (!cid) {
    content.innerHTML = '<div class="empty">这台设备还没绑定孩子。<br>点顶部「注入演示数据」，或「＋设备」新建。</div>';
    return;
  }
  const home = await get(`/api/child/${cid}/home`);
  if (home.error) { content.innerHTML = `<div class="empty">加载失败：${esc(home.detail || home.error)}</div>`; return; }
  renderHome(slot, home);
}

async function submitAnswer(slot, cid, value) {
  const r = await post(`/api/child/${slots[slot].cid}/answer`, { cid, answer: value });
  if (r.error) { alert("出错了：" + (r.detail || r.error)); return; }
  slots[slot].outcome = r.outcome || null;
  slots[slot].events = r.events || [];
  renderHome(slot, r.home);
}

async function toggleReport(slot) {
  const p = panel(slot);
  const rep = p.querySelector(".report-panel");
  const content = p.querySelector(".device-content");
  if (slots[slot].view === "report") {
    slots[slot].view = "device";
    rep.classList.add("hidden"); content.classList.remove("hidden");
    p.querySelector(".view-toggle").textContent = "家长后台";
    return;
  }
  if (!slots[slot].cid) { alert("先绑定一个孩子"); return; }
  const data = await get(`/api/child/${slots[slot].cid}/report`);
  rep.innerHTML = reportView(data);
  slots[slot].view = "report";
  rep.classList.remove("hidden"); content.classList.add("hidden");
  p.querySelector(".view-toggle").textContent = "返回设备";
}

function reportView(rep) {
  const w = rep.this_week;
  const rows = rep.abilities.map((a) => {
    const cls = a.delta > 0 ? "up" : "flat";
    const acc = a.accuracy == null ? "—（练习量）" : a.accuracy + "%";
    return `<tr><td>${esc(a.ability_zh)}</td><td>${a.week_ago}</td><td>${a.now}</td>
      <td class="delta ${cls}">${a.delta > 0 ? "+" + a.delta : a.delta}</td>
      <td>${a.practiced}</td><td>${acc}</td></tr>`;
  }).join("");
  const hls = (rep.highlights || []).map((h) =>
    `<div class="rep-hl"><div class="q">${esc(h.prompt)}</div>“${esc(h.answer)}”</div>`).join("")
    || '<span class="muted">本周还没有高光片段，多做几次表达/创造挑战就有了。</span>';
  const imp = rep.biggest_improvement
    ? `${esc(rep.biggest_improvement.ability_zh)} +${rep.biggest_improvement.delta}` : "—";
  return `
    <h3>👪 ${esc(rep.name)} · 本周成长报告</h3>
    <table class="rep-abil">
      <tr><th>能力</th><th>周初</th><th>现在</th><th>变化</th><th>练习</th><th>正确率</th></tr>
      ${rows}
    </table>
    <div class="rep-week">
      <div class="stat"><b>${w.completed_days}</b>完成天数</div>
      <div class="stat"><b>${w.challenges_done}</b>挑战数</div>
      <div class="stat"><b>${w.streak}</b>当前连续</div>
      <div class="stat"><b>${w.best_streak}</b>最佳连续</div>
      <div class="stat"><b>${w.stars}</b>智慧星</div>
    </div>
    <div style="font-size:13px;margin-bottom:8px">📈 最大进步：<b>${imp}</b>　🏅 徽章：${(w.badges||[]).map(esc).join("、")||"暂无"}</div>
    <div style="font-weight:700;font-size:13px;margin:6px 0">✨ 本周亮点（最真实的几句）</div>
    ${hls}
    <div class="rep-note">${esc(rep.honest_note)}</div>`;
}

/* ---------------- 对战 ---------------- */
async function bump() {
  if (!slots.A.cid || !slots.B.cid) { alert("需要两台设备各绑定一个孩子"); return; }
  if (slots.A.cid === slots.B.cid) { alert("两台设备要选不同的孩子"); return; }
  const res = await post("/api/battle", { a: slots.A.cid, b: slots.B.cid });
  if (res.error) { alert("对战失败：" + (res.detail || res.error)); return; }
  showBattle(res);
  renderHome("A", res.a_home);
  renderHome("B", res.b_home);
}

function showBattle(res) {
  const card = $("#modal-card");
  const wname = res.winner === "a" ? res.a_name : res.b_name;
  card.innerHTML = `
    <div class="bt-head">
      <div class="bt-side"><div class="nm">${esc(res.a_name)}</div><div class="pw">战力 ${res.a_power}</div><div class="rk">${esc(res.a_rank)}</div></div>
      <div class="bt-vs">VS</div>
      <div class="bt-side"><div class="nm">${esc(res.b_name)}</div><div class="pw">战力 ${res.b_power}</div><div class="rk">${esc(res.b_rank)}</div></div>
    </div>
    ${res.friendly ? '<div class="bt-friendly">🤝 段位/战力悬殊 → 友谊赛：弱的一方有加成，输了也不掉成长值</div>' : ""}
    <div id="bt-rounds"></div>
    <div class="bt-narr hidden" id="bt-narr">${esc(res.narration)}</div>
    <div class="bt-reward hidden" id="bt-reward">
      <span>${esc(res.a_name)}：+${res.a_reward.stars}⭐ +${res.a_reward.growth}成长</span>
      <span>${esc(res.b_name)}：+${res.b_reward.stars}⭐ +${res.b_reward.growth}成长</span>
    </div>
    <div class="bt-actions">
      <button id="bt-again">再碰一碰</button>
      <button id="bt-close" class="ghost">关闭</button>
    </div>`;
  $("#modal").classList.remove("hidden");
  const rounds = $("#bt-rounds");
  res.rounds.forEach((rd, i) => {
    const div = document.createElement("div");
    div.className = "bt-round";
    div.innerHTML = `<span class="rl">${esc(rd.label)}</span>
      <span class="rr ${rd.winner === "a" ? "win" : ""}">${rd.a_roll}</span>
      <span style="color:#8b93ab">—</span>
      <span class="rr ${rd.winner === "b" ? "win" : ""}">${rd.b_roll}</span>`;
    rounds.appendChild(div);
    setTimeout(() => div.classList.add("show"), 250 * (i + 1));
  });
  setTimeout(() => {
    $("#bt-narr").classList.remove("hidden");
    $("#bt-reward").classList.remove("hidden");
  }, 250 * (res.rounds.length + 1));
  $("#bt-again").onclick = () => { $("#modal").classList.add("hidden"); bump(); };
  $("#bt-close").onclick = () => $("#modal").classList.add("hidden");
}

/* ---------------- 选择器 / 顶栏 ---------------- */
async function refreshSelectors(keep = true) {
  const data = await get("/api/children");
  const kids = data.children || [];
  const has = (id) => id && kids.some((k) => k.child_id === id);
  // 期望选中：保留旧选择；否则 A=第一个、B=第二个（没有第二个就退回第一个）
  const want = {
    A: (keep && has(slots.A.cid)) ? slots.A.cid : (kids[0] && kids[0].child_id) || "",
    B: (keep && has(slots.B.cid)) ? slots.B.cid : ((kids[1] || kids[0] || {}).child_id) || "",
  };
  for (const slot of ["A", "B"]) {
    const sel = panel(slot).querySelector(".child-select");
    sel.innerHTML = kids.length
      ? kids.map((k) => `<option value="${k.child_id}">${esc(k.name)}（${esc(k.rank)}·战力${k.power}·🔥${k.streak}）</option>`).join("")
      : '<option value="">（暂无设备）</option>';
    sel.value = want[slot];
    slots[slot].cid = want[slot] || null;
  }
  if (data.day) $("#clock").textContent = "📅 " + data.day;
}

async function refreshClock() {
  const c = await get("/api/clock");
  $("#clock").textContent = "📅 " + c.day;
}

async function newChild(slot) {
  const name = prompt("孩子的名字？", "小朋友");
  if (name === null) return;
  const grade = parseInt(prompt("几年级？(1-3)", "2") || "2", 10);
  const age = 5 + grade;
  const r = await post("/api/children", { name, grade, age });
  if (r.error) { alert("创建失败：" + (r.detail || r.error)); return; }
  await refreshSelectors();
  slots[slot].cid = r.child_id;
  panel(slot).querySelector(".child-select").value = r.child_id;
  await loadSlot(slot);
}

function wire() {
  document.body.addEventListener("click", (e) => {
    const sub = e.target.closest(".sub");
    if (sub) {
      const slot = sub.closest(".device").dataset.slot;
      const cid = sub.dataset.cid;
      const input = sub.parentElement.querySelector("input");
      submitAnswer(slot, cid, input.value.trim());
      return;
    }
    if (e.target.closest(".newchild")) { newChild(e.target.closest(".device").dataset.slot); return; }
    if (e.target.closest(".view-toggle")) { toggleReport(e.target.closest(".device").dataset.slot); return; }
  });
  document.body.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.matches(".canswer input")) {
      const card = e.target.closest(".device");
      submitAnswer(card.dataset.slot, e.target.dataset.cid, e.target.value.trim());
    }
  });
  for (const slot of ["A", "B"]) {
    panel(slot).querySelector(".child-select").addEventListener("change", (e) => {
      slots[slot].cid = e.target.value || null;
      slots[slot].outcome = null; slots[slot].events = [];
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
  wire();
  await refreshClock();
  await refreshSelectors();
  await loadSlot("A");
  await loadSlot("B");
}
boot();
