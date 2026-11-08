"use strict";
/* ScholarPilot frontend. Plain JavaScript, no build step. Every screen uses the real API;
   nothing is mocked. Sections: helpers, auth, shell, dashboard, chat, matches, plan,
   tracker, drafts, scam check, profile, review, boot. */

// ======================= helpers =======================
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const state = {
  token: null, user: null, view: null, conversationId: null, sending: false,
  matches: [], apps: [], drafts: [], smtp: false, profile: null,
  selected: new Set(), activeDraft: null, reviewStatus: "pending", trackerFilter: "",
  filters: { q: "", country: "", status: "", funding: "", verdict: "", sort: "match" },
};

function store(key, value) { try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch (_) { /* storage blocked */ } }
function load(key) { try { return localStorage.getItem(key); } catch (_) { return null; } }
function loadJSON(key, fallback) { try { return JSON.parse(load(key)) ?? fallback; } catch (_) { return fallback; } }
const userKey = name => `sp_${name}_${state.user?.id}`;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function parseISO(iso) { if (!iso) return null; const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number); return y ? new Date(y, m - 1, d) : null; }
function fmtDate(iso) { const d = parseISO(iso); return d ? `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}` : ""; }
function daysUntil(iso) {
  const d = parseISO(iso); if (!d) return null;
  const t = new Date(); t.setHours(0, 0, 0, 0);
  return Math.round((d - t) / 86400000);
}
function relDays(n) {
  if (n === null) return "";
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n === -1) return "yesterday";
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}
const initials = name => String(name || "?").trim().split(/\s+/).slice(0, 2).map(w => w[0]).join("").toUpperCase();

async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let res;
  try { res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined }); }
  catch (_) { throw new Error("Can't reach the server. Check your connection and try again."); }
  if (res.status === 401 && state.token) { logout(); throw new Error("Your session expired. Please log in again."); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(d => d.msg.replace(/^Value error, /, "")).join("; ") : data.detail;
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data;
}

// Toasts: type = success | error | info
function toast(text, type = "info") {
  const icons = { success: "check-circle", error: "alert", info: "info" };
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.setAttribute("role", type === "error" ? "alert" : "status");
  el.innerHTML = `${icon(icons[type])}<div>${esc(text)}</div>`;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 260); }, type === "error" ? 6000 : 3800);
}

// Confirmation dialog. Resolves true/false.
function confirmDialog({ title, text = "", ok = "Confirm", danger = false, iconName = "help" }) {
  const modal = $("#modal");
  $("#modal-title").textContent = title;
  $("#modal-text").textContent = text;
  $("#modal-icon").className = `modal-icon${danger ? " danger" : ""}`;
  $("#modal-icon").innerHTML = icon(iconName);
  const okBtn = $("#modal-ok");
  okBtn.textContent = ok;
  okBtn.classList.toggle("danger", danger);
  modal.classList.remove("hidden");
  const prevFocus = document.activeElement;
  okBtn.focus();
  return new Promise(resolve => {
    const done = v => {
      modal.classList.add("hidden");
      okBtn.onclick = $("#modal-cancel").onclick = modal.onclick = null;
      document.removeEventListener("keydown", onKey);
      prevFocus?.focus?.();
      resolve(v);
    };
    const onKey = e => { if (e.key === "Escape") done(false); };
    okBtn.onclick = () => done(true);
    $("#modal-cancel").onclick = () => done(false);
    modal.onclick = e => { if (e.target === modal) done(false); };
    document.addEventListener("keydown", onKey);
  });
}

function setLoading(btn, on) {
  if (!btn) return;
  btn.classList.toggle("loading", on);
  btn.disabled = on;
}

function emptyState(img, title, text, action = "", small = false) {
  return `<div class="empty${small ? " small" : ""}">${img ? `<img src="/static/assets/images/${img}" alt="" loading="lazy">` : ""}
    <h3>${esc(title)}</h3><p>${esc(text)}</p>${action}</div>`;
}
const skeletonCards = n => Array.from({ length: n }, () => `<div class="skel skel-card"></div>`).join("");
const skeletonLines = n => Array.from({ length: n }, (_, i) => `<div class="skel skel-line" style="width:${90 - i * 12}%"></div>`).join("");

// ---------- markdown (escape first, then format): headings, lists, tables, quotes, links ----------
function inlineMd(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:])/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
}
function renderMarkdown(src) {
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  let html = "", list = null, para = [];
  const flushPara = () => { if (para.length) { html += `<p>${para.map(inlineMd).join("<br>")}</p>`; para = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  const isRow = l => /^\s*\|.*\|\s*$/.test(l);
  const cells = l => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd();
    let m;
    if (/^\s*```/.test(line)) {
      // fenced block: keep the text as written
      flushPara(); closeList();
      const body = [];
      while (i + 1 < lines.length && !/^\s*```/.test(lines[i + 1])) { i += 1; body.push(lines[i]); }
      i += 1;
      html += `<pre class="md-pre">${esc(body.join("\n"))}</pre>`;
    } else if (isRow(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flushPara(); closeList();
      const head = cells(line);
      i += 1;
      const rows = [];
      while (i + 1 < lines.length && isRow(lines[i + 1])) { i += 1; rows.push(cells(lines[i])); }
      html += `<div class="table-wrap"><table><thead><tr>${head.map(h => `<th>${inlineMd(h)}</th>`).join("")}</tr></thead><tbody>${
        rows.map(r => `<tr>${head.map((_, k) => `<td>${inlineMd(r[k] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    } else if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
      flushPara(); if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${inlineMd(m[1])}</li>`;
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      flushPara(); if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${inlineMd(m[1])}</li>`;
    } else if ((m = line.match(/^(#{1,6})\s+(.*)/))) {
      flushPara(); closeList();
      const level = Math.min(5, Math.max(3, m[1].length + 1));
      html += `<h${level}>${inlineMd(m[2])}</h${level}>`;
    } else if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushPara(); closeList(); html += "<hr>";
    } else if ((m = line.match(/^\s*>\s?(.*)/))) {
      flushPara(); closeList(); html += `<blockquote>${inlineMd(m[1])}</blockquote>`;
    } else if (!line.trim()) {
      flushPara(); closeList();
    } else {
      closeList(); para.push(line);
    }
  }
  flushPara(); closeList();
  return html;
}

// ======================= auth =======================
function showAuth() {
  $("#auth-view").classList.remove("hidden");
  $("#app-view").classList.add("hidden");
  document.title = "ScholarPilot: funded scholarships for Pakistani students";
  loadPublicStats();
}

function logout() {
  state.token = null; state.user = null; state.conversationId = null; state.selected = new Set();
  store("sp_token", null);
  history.replaceState(null, "", location.pathname);
  showAuth();
}

function moveSeg(seg) {
  const active = $(".seg-btn.active", seg), ind = $(".seg-indicator", seg);
  if (!active || !ind) return;
  ind.style.width = `${active.offsetWidth}px`;
  ind.style.transform = `translateX(${active.offsetLeft - 4}px)`;
}
window.addEventListener("resize", () => $$(".seg").forEach(moveSeg));

$$("[data-auth]").forEach(btn => btn.addEventListener("click", () => {
  const mode = btn.dataset.auth;
  $$("[data-auth]").forEach(b => { b.classList.toggle("active", b === btn); b.setAttribute("aria-selected", b === btn); });
  moveSeg(btn.parentElement);
  ["login", "signup"].forEach(f => {
    const form = $(`#${f}-form`);
    form.classList.toggle("hidden", f !== mode);
    if (f === mode) { form.classList.remove("form-anim"); void form.offsetWidth; form.classList.add("form-anim"); }
  });
  $("#auth-title").textContent = mode === "login" ? "Welcome back" : "Create your account";
  $("#auth-sub").textContent = mode === "login" ? "Log in to continue your scholarship plan." : "Free. Takes under a minute.";
  hideAuthError();
}));

$$(".pw-toggle").forEach(b => b.addEventListener("click", () => {
  const input = b.parentElement.querySelector("input");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  b.setAttribute("aria-label", show ? "Hide password" : "Show password");
  setIcon(b, show ? "eye-off" : "eye");
}));

$("#su-password").addEventListener("input", e => {
  const v = e.target.value;
  const score = (v.length >= 8) + (v.length >= 12) + (/[A-Z]/.test(v) && /[a-z]/.test(v)) + /\d/.test(v) + /[^A-Za-z0-9]/.test(v);
  const bar = $(".pw-meter span");
  bar.style.width = `${Math.min(100, score * 20)}%`;
  bar.style.background = score <= 1 ? "var(--bad)" : score <= 3 ? "#f59e0b" : "var(--ok)";
});

function fieldError(input, msg) {
  const field = input.closest(".field");
  field.classList.toggle("invalid", !!msg);
  const out = $(".field-error", field);
  if (out) out.textContent = msg || "";
}
function validateAuth(form) {
  let ok = true;
  $$("input[name]", form).forEach(input => {
    if (input.type === "radio") return;
    let msg = "";
    const v = input.value.trim();
    if (input.required && !v) msg = "This field is required.";
    else if (input.type === "email" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) msg = "Enter a valid email address.";
    else if (input.minLength > 0 && v.length < input.minLength) msg = input.name === "password" ? "Use at least 8 characters." : `At least ${input.minLength} characters.`;
    fieldError(input, msg);
    if (msg) ok = false;
  });
  const level = $("[name=target_level]", form);
  if (level && !$("[name=target_level]:checked", form)) {
    $(".field-error", level.closest(".field")).textContent = "Choose Bachelor's or Master's.";
    ok = false;
  } else if (level) { $(".field-error", level.closest(".field")).textContent = ""; }
  return ok;
}
$$("#login-form input, #signup-form input").forEach(i => i.addEventListener("input", () => { if (i.type !== "radio") fieldError(i, ""); }));
$$("[name=target_level]").forEach(r => r.addEventListener("change", () => { $(".field-error", r.closest(".field")).textContent = ""; }));

function showAuthError(msg) { const el = $("#auth-error"); el.textContent = msg; el.classList.remove("hidden"); }
function hideAuthError() { $("#auth-error").classList.add("hidden"); }

async function handleAuth(form, path) {
  hideAuthError();
  if (!validateAuth(form)) return;
  const btn = $("button[type=submit]", form);
  setLoading(btn, true);
  const body = Object.fromEntries(new FormData(form));
  try {
    const data = await api(path, { method: "POST", body });
    state.token = data.token; state.user = data.user;
    store("sp_token", data.token);
    toast(path.endsWith("signup") ? "Account created. Welcome to ScholarPilot!" : `Welcome back, ${data.user.full_name.split(" ")[0]}!`, "success");
    form.reset();
    startApp();
  } catch (e) { showAuthError(e.message); }
  finally { setLoading(btn, false); }
}
$("#login-form").addEventListener("submit", e => { e.preventDefault(); handleAuth(e.target, "/api/auth/login"); });
$("#signup-form").addEventListener("submit", e => { e.preventDefault(); handleAuth(e.target, "/api/auth/signup"); });

async function loadPublicStats() {
  const box = $("#auth-stats");
  try {
    const s = await api("/api/public/stats");
    const soon = s.closing_soon[0];
    box.innerHTML = `
      <div class="float-card"><strong>${s.scholarships}</strong><small>verified scholarships</small></div>
      <div class="float-card"><strong>${s.destinations}</strong><small>study destinations</small></div>
      ${soon ? `<div class="float-card wide"><span class="fc-tag">${icon("clock")}${soon.status === "open" ? "Open now" : "Opens soon"}</span>
        <span class="fc-name">${esc(soon.name)}</span><small>Deadline ${esc(fmtDate(soon.deadline))} · ${esc(soon.country)}</small></div>` : ""}`;
  } catch (_) { box.innerHTML = ""; }
}

// ======================= app shell =======================
const VIEWS = {
  dashboard: ["Dashboard", "Your scholarship journey at a glance"],
  chat: ["Assistant", "Ask anything about funded scholarships"],
  matches: ["Matches", "Scholarships checked against your profile"],
  plan: ["Application plan", "Deadlines, documents and steps"],
  tracker: ["Application tracker", "Where each application stands"],
  drafts: ["Email drafts", "Nothing is sent until you approve it"],
  scam: ["Scam check", "Paste a message or link before you trust it"],
  profile: ["Your profile", "The facts we use to check eligibility"],
  review: ["Catalog review", "Changes found on official pages"],
};
const loaders = {
  dashboard: loadDashboard, chat: loadConversations, matches: loadMatches, plan: renderPlan, tracker: loadTracker,
  drafts: loadDrafts, scam: () => {}, profile: loadProfile, review: loadReviews,
};

function showView(name, { push = true } = {}) {
  if (!VIEWS[name] || (name === "review" && !state.user?.is_admin)) name = "dashboard";
  state.view = name;
  $$("#nav button").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  $$("[data-panel]").forEach(p => p.classList.toggle("hidden", p.dataset.panel !== name));
  const [title, sub] = VIEWS[name];
  $("#page-title").textContent = title;
  $("#page-sub").textContent = sub;
  document.title = `${title} · ScholarPilot`;
  if (push && location.hash !== `#${name}`) history.pushState(null, "", `#${name}`);
  closeNav(); closeDropdown();
  window.scrollTo({ top: 0 });
  Promise.resolve(loaders[name]?.()).catch(e => toast(e.message, "error"));
}
window.addEventListener("popstate", () => { if (state.user) showView(location.hash.slice(1) || "dashboard", { push: false }); });
$$("#nav button").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));
document.addEventListener("click", e => {
  const go = e.target.closest("[data-go]");
  if (go) { e.preventDefault(); showView(go.dataset.go); }
});

function openNav() { $("#app-view").classList.add("nav-open"); }
function closeNav() { $("#app-view").classList.remove("nav-open"); $("#chat-history").classList.remove("open"); }
$("#menu-btn").addEventListener("click", openNav);
$("#sidebar-close").addEventListener("click", closeNav);
$("#scrim").addEventListener("click", closeNav);

function closeDropdown() { $("#user-dropdown").classList.add("hidden"); $("#user-btn").setAttribute("aria-expanded", "false"); }
$("#user-btn").addEventListener("click", e => {
  e.stopPropagation();
  const dd = $("#user-dropdown");
  const open = dd.classList.contains("hidden");
  dd.classList.toggle("hidden", !open);
  $("#user-btn").setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", e => { if (!e.target.closest(".user-menu")) closeDropdown(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") { closeDropdown(); closeNav(); } });
$("#logout").addEventListener("click", async () => {
  closeDropdown();
  if (await confirmDialog({ title: "Log out?", text: "You can log back in any time.", ok: "Log out", iconName: "logout" })) logout();
});
window.addEventListener("scroll", () => $(".topbar").classList.toggle("scrolled", window.scrollY > 4), { passive: true });

async function startApp() {
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  const u = state.user;
  $("#who-name").textContent = u.full_name;
  $("#who-level").textContent = `${u.target_level === "bachelor" ? "Bachelor's" : "Master's"} · ${u.role[0].toUpperCase()}${u.role.slice(1)}`;
  $("#avatar").textContent = initials(u.full_name);
  $("#nav-review").classList.toggle("hidden", !u.is_admin);
  state.selected = new Set(loadJSON(userKey("plan_ids"), []));
  showView(location.hash.slice(1) || "dashboard", { push: false });
  refreshDraftCount();
}

// ======================= dashboard =======================
const PROFILE_KEYS = {
  bachelor: ["nationality", "date_of_birth", "current_education", "intended_field", "grade_percent", "english", "financial_need", "preferred_countries"],
  master: ["nationality", "date_of_birth", "field_of_study", "intended_field", "cgpa", "graduation_year", "english", "work_experience_months", "financial_need", "preferred_countries"],
};
const KEY_LABELS = {
  nationality: "Nationality", date_of_birth: "Date of birth", current_education: "Current qualification", intended_field: "Field you want to study",
  grade_percent: "HSSC / A-Level percentage", english: "An English test score", financial_need: "Financial need", preferred_countries: "Preferred countries",
  field_of_study: "Field studied", cgpa: "CGPA", graduation_year: "Graduation year", work_experience_months: "Work experience",
};
function profileStrength(p) {
  const keys = PROFILE_KEYS[state.user.target_level];
  const has = k => k === "english" ? ["ielts", "toefl_ibt", "pte", "duolingo"].some(t => p[t] != null)
    : Array.isArray(p[k]) ? p[k].length > 0 : p[k] !== null && p[k] !== undefined && p[k] !== "";
  const missing = keys.filter(k => !has(k));
  return { pct: Math.round(((keys.length - missing.length) / keys.length) * 100), missing };
}
function ringSvg(pct) {
  const r = 40, c = 2 * Math.PI * r;
  return `<svg class="ring" viewBox="0 0 100 100" role="img" aria-label="${pct}% complete"><circle class="bg" cx="50" cy="50" r="${r}"/>
    <circle class="fg" cx="50" cy="50" r="${r}" stroke-dasharray="${c}" stroke-dashoffset="${c}" data-target="${c * (1 - pct / 100)}"/>
    <text x="50" y="57" text-anchor="middle">${pct}%</text></svg>`;
}
function animateRings(root) { requestAnimationFrame(() => $$(".ring .fg", root).forEach(c => { c.style.strokeDashoffset = c.dataset.target; })); }
function strengthHtml(p) {
  const { pct, missing } = profileStrength(p);
  return `<div class="ring-wrap">${ringSvg(pct)}<div>${pct === 100
    ? `<strong>Complete.</strong><p class="muted" style="margin:0">Eligibility checks can use everything they need.</p>`
    : `<strong>Add to improve your checks:</strong><ul class="missing-list">${missing.slice(0, 4).map(k => `<li>${esc(KEY_LABELS[k])}</li>`).join("")}${missing.length > 4 ? `<li>${missing.length - 4} more</li>` : ""}</ul>`}</div></div>`;
}

async function loadDashboard() {
  const first = state.user.full_name.split(" ")[0];
  $("#welcome-title").textContent = `Assalam-o-Alaikum, ${first}!`;
  $("#welcome-level").textContent = `${state.user.target_level === "bachelor" ? "Bachelor's" : "Master's"} scholarships`;
  $("#stat-grid").innerHTML = Array.from({ length: 4 }, () => `<div class="skel" style="height:140px;border-radius:14px"></div>`).join("");
  ["#dash-deadlines", "#dash-actions", "#dash-profile"].forEach(s => { $(s).innerHTML = skeletonLines(4); });
  const [matches, apps, draftData, profile] = await Promise.all([api("/api/matches"), api("/api/applications"), api("/api/drafts"), api("/api/profile")]);
  state.matches = matches; state.apps = apps; state.profile = profile;
  const eligible = matches.filter(m => m.verdict === "eligible").length;
  const likely = matches.filter(m => m.verdict === "likely_eligible").length;
  const active = apps.filter(a => !["accepted", "rejected", "withdrawn"].includes(a.status)).length;
  const pendingDrafts = draftData.drafts.filter(d => d.status === "pending").length;
  const openNow = matches.filter(m => ["open", "upcoming"].includes(m.deadline_status)).length;
  const stats = [
    ["teal", "search", matches.length, "Scholarships for your level", "matches", 100, `${openNow} open or opening soon`],
    ["green", "check-circle", eligible + likely, "Eligible or likely eligible", "matches", matches.length ? ((eligible + likely) / matches.length) * 100 : 0, `${eligible} fully eligible`],
    ["blue", "list", active, "Applications in progress", "tracker", apps.length ? (active / apps.length) * 100 : 0, `${apps.length} tracked in total`],
    ["violet", "mail", pendingDrafts, "Drafts awaiting approval", "drafts", pendingDrafts ? 100 : 0, pendingDrafts ? "Review before sending" : "Nothing waiting"],
  ];
  $("#stat-grid").innerHTML = stats.map(([tone, ic, n, label, go, pct, sub]) => `
    <button class="stat tone-${tone}" data-go="${go}"><span class="stat-icon">${icon(ic)}</span><strong>${n}</strong>
      <span class="lbl">${label}</span><div class="bar"><i data-w="${Math.round(pct)}"></i></div><small class="muted">${sub}</small></button>`).join("");
  requestAnimationFrame(() => $$("#stat-grid .bar i").forEach(i => { i.style.width = `${i.dataset.w}%`; }));

  const upcoming = matches.filter(m => m.next_deadline && m.deadline_status !== "closed")
    .sort((a, b) => a.next_deadline.localeCompare(b.next_deadline)).slice(0, 5);
  $("#dash-deadlines").innerHTML = upcoming.length ? `<ul class="mini-list">${upcoming.map(m => {
    const d = parseISO(m.next_deadline);
    return `<li data-go="matches"><span class="date-pill"><b>${d.getDate()}</b><small>${MONTHS[d.getMonth()]}</small></span>
      <span class="grow"><strong>${esc(m.scholarship_name)}</strong><small>${esc(m.country)} · ${relDays(daysUntil(m.next_deadline))}${m.deadline_approximate ? " · approximate" : ""}</small></span>
      <span class="badge b-${esc(m.deadline_status)}">${statusLabel(m.deadline_status)}</span></li>`;
  }).join("")}</ul>` : emptyState("", "No dated deadlines", "Scholarships with course-set deadlines are listed in Matches.", "", true);

  const actions = apps.filter(a => a.next_action).sort((a, b) => (a.next_action_date || "9999").localeCompare(b.next_action_date || "9999")).slice(0, 5);
  $("#dash-actions").innerHTML = actions.length ? `<ul class="mini-list">${actions.map(a => {
    const n = daysUntil(a.next_action_date), d = parseISO(a.next_action_date);
    return `<li data-go="tracker">${d ? `<span class="date-pill ${n < 0 ? "late" : ""}"><b>${d.getDate()}</b><small>${MONTHS[d.getMonth()]}</small></span>` : `<span class="date-pill">${icon("flag")}</span>`}
      <span class="grow"><strong>${esc(a.next_action)}</strong><small>${esc(a.scholarship_name || a.scholarship_id)}${n !== null ? ` · ${relDays(n)}` : ""}</small></span></li>`;
  }).join("")}</ul>` : emptyState("", "No actions yet", "Track a scholarship and set your next step.", `<button class="btn btn-ghost btn-sm" data-go="matches">Find scholarships</button>`, true);

  $("#dash-profile").innerHTML = strengthHtml(profile);
  animateRings($("#dash-profile"));
}

// ======================= chat =======================
const SUGGESTIONS = {
  bachelor: [["search", "Which bachelor's scholarships fit my profile?"], ["cap", "Am I eligible for MEXT?"], ["doc", "What documents do I need for Stipendium Hungaricum?"], ["shield", "How do I get my documents attested by IBCC?"]],
  master: [["search", "Which fully funded master's scholarships fit me?"], ["cap", "Am I eligible for Chevening?"], ["calendar", "Make me a timeline for the Commonwealth Shared Scholarship"], ["shield", "How do I get my degree attested by HEC?"]],
};
const THINKING = ["Reading your profile", "Searching the verified catalog", "Checking eligibility rules", "Checking official sources", "Writing your answer"];

async function loadConversations() {
  const convos = await api("/api/conversations");
  const ul = $("#convo-list");
  ul.innerHTML = convos.length ? convos.map(c => `<li data-id="${c.id}" class="${c.id === state.conversationId ? "active" : ""}">
      <button class="title" title="${esc(c.title)}">${esc(c.title)}</button>
      <button class="del" aria-label="Delete chat">${icon("trash")}</button></li>`).join("")
    : `<li class="muted" style="padding:.5rem;font-size:.84rem">No chats yet.</li>`;
  $$("li[data-id]", ul).forEach(li => {
    $(".title", li).addEventListener("click", () => { openConversation(Number(li.dataset.id)); $("#chat-history").classList.remove("open"); });
    $(".del", li).addEventListener("click", async () => {
      if (!await confirmDialog({ title: "Delete this chat?", text: "The conversation will be removed. This can't be undone.", ok: "Delete", danger: true, iconName: "trash" })) return;
      try {
        await api(`/api/conversations/${li.dataset.id}`, { method: "DELETE" });
        if (Number(li.dataset.id) === state.conversationId) state.conversationId = null;
        toast("Chat deleted.", "success");
        loadConversations();
      } catch (e) { toast(e.message, "error"); }
    });
  });
  if (state.conversationId === null) {
    if (convos.length) await openConversation(convos[0].id); else renderMessages([]);
  }
}

async function openConversation(id) {
  state.conversationId = id;
  $$("#convo-list li").forEach(li => li.classList.toggle("active", Number(li.dataset.id) === id));
  $("#messages").innerHTML = `<div style="padding:1rem">${skeletonLines(3)}</div>`;
  const c = await api(`/api/conversations/${id}`);
  renderMessages(c.messages);
}

const BOT_AVATAR = `<img class="msg-avatar" src="/static/assets/logo.svg" alt="">`;
function messageHtml(m) {
  if (m.role === "user") return `<div class="msg-row user"><div class="msg">${esc(m.text)}</div></div>`;
  const tools = [...new Set(m.tools || [])].filter(t => t !== "open_tools");
  const foot = `<div class="msg-foot">${tools.map(t => `<span class="chip">${icon("check")}${esc(t.replace(/_/g, " "))}</span>`).join("")}
    <button class="copy" type="button" aria-label="Copy answer">${icon("copy")}Copy</button></div>`;
  return `<div class="msg-row assistant">${BOT_AVATAR}<div class="msg${m.error ? " error" : ""}" data-raw="${esc(m.text)}">${m.error ? esc(m.text) : renderMarkdown(m.text)}${m.error ? "" : foot}</div></div>`;
}

function renderMessages(messages) {
  const box = $("#messages");
  if (!messages.length) {
    box.innerHTML = `<div class="chat-welcome"><img src="/static/assets/images/empty-chat.jpg" alt="">
      <h2>Assalam-o-Alaikum, ${esc(state.user.full_name.split(" ")[0])}!</h2>
      <p class="muted">Tell me your grades, tests and plans. I'll find funded scholarships you qualify for, explain why, and build a timeline, using official sources only.</p>
      <div class="suggestions">${SUGGESTIONS[state.user.target_level].map(([ic, s]) => `<button type="button" class="sugg">${icon(ic)}<span>${esc(s)}</span></button>`).join("")}</div></div>`;
    $$(".sugg", box).forEach(b => b.addEventListener("click", () => { chatInput.value = b.textContent.trim(); $("#chat-form").requestSubmit(); }));
  } else {
    box.innerHTML = messages.map(messageHtml).join("");
  }
  scrollChat(false);
}
function scrollChat(smooth = true) { const box = $("#messages"); box.scrollTo({ top: box.scrollHeight, behavior: smooth ? "smooth" : "auto" }); }

$("#messages").addEventListener("click", async e => {
  const btn = e.target.closest(".copy");
  if (!btn) return;
  const raw = btn.closest(".msg").dataset.raw;
  try { await navigator.clipboard.writeText(raw); toast("Answer copied.", "success"); }
  catch (_) { toast("Couldn't copy. Select the text instead.", "error"); }
});

$("#new-chat").addEventListener("click", async () => {
  try {
    const c = await api("/api/conversations", { method: "POST" });
    state.conversationId = c.id;
    await loadConversations();
    renderMessages([]);
    $("#chat-history").classList.remove("open");
    $("#chat-input").focus();
  } catch (e) { toast(e.message, "error"); }
});
$("#history-toggle").addEventListener("click", () => $("#chat-history").classList.toggle("open"));

const chatInput = $("#chat-input");
function autoGrow() { chatInput.style.height = "auto"; chatInput.style.height = `${Math.min(180, chatInput.scrollHeight)}px`; }
chatInput.addEventListener("input", autoGrow);
chatInput.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); } });

$("#chat-form").addEventListener("submit", async e => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text || state.sending) return;
  const box = $("#messages");
  state.sending = true;
  $("#send-btn").disabled = true;
  try {
    if (state.conversationId === null) state.conversationId = (await api("/api/conversations", { method: "POST" })).id;
  } catch (err) { toast(err.message, "error"); state.sending = false; $("#send-btn").disabled = false; return; }
  if ($(".chat-welcome", box)) box.innerHTML = "";
  box.insertAdjacentHTML("beforeend", messageHtml({ role: "user", text }));
  box.insertAdjacentHTML("beforeend", `<div class="msg-row assistant thinking-row">${BOT_AVATAR}<div class="msg"><div class="typing"><span class="dots"><i></i><i></i><i></i></span><span class="t-text">${THINKING[0]}…</span></div></div></div>`);
  chatInput.value = ""; autoGrow();
  scrollChat();
  let step = 0;
  const ticker = setInterval(() => { step = Math.min(step + 1, THINKING.length - 1); const t = $(".thinking-row .t-text", box); if (t) t.textContent = `${THINKING[step]}…`; }, 2600);
  try {
    const r = await api(`/api/conversations/${state.conversationId}/messages`, { method: "POST", body: { text } });
    $(".thinking-row", box)?.remove();
    box.insertAdjacentHTML("beforeend", messageHtml({ role: "assistant", text: r.reply, tools: r.tool_calls.map(t => t.tool) }));
    if (r.created_drafts.length) { toast("A new email draft is waiting for your approval in Drafts.", "info"); refreshDraftCount(); }
    loadConversations();
  } catch (err) {
    $(".thinking-row", box)?.remove();
    box.insertAdjacentHTML("beforeend", messageHtml({ role: "assistant", text: err.message, error: true }));
  } finally {
    clearInterval(ticker);
    state.sending = false;
    $("#send-btn").disabled = false;
    scrollChat();
  }
});

// ======================= matches =======================
const VERDICT = {
  eligible: ["Eligible", "check-circle"], likely_eligible: ["Likely eligible", "help"], not_eligible: ["Not eligible yet", "x-circle"],
};
const CRIT_ICON = { pass: "check-circle", fail: "x-circle", unknown: "help" };
function statusLabel(s) { return { open: "Open now", upcoming: "Opening soon", closed: "Closed", varies: "Set by course" }[s] || "Check page"; }

async function loadMatches() {
  const list = $("#matches-list");
  list.innerHTML = skeletonCards(6);
  const [matches, apps] = await Promise.all([api("/api/matches"), api("/api/applications")]);
  state.matches = matches; state.apps = apps;
  // keep only selections that still exist
  state.selected = new Set([...state.selected].filter(id => matches.some(m => m.scholarship_id === id)));
  const countries = [...new Set(matches.map(m => m.country))].sort();
  $("#m-country").innerHTML = `<option value="">All countries</option>${countries.map(c => `<option ${state.filters.country === c ? "selected" : ""}>${esc(c)}</option>`).join("")}`;
  renderMatches();
  updateActionBar();
}

function filteredMatches() {
  const f = state.filters, q = f.q.toLowerCase();
  let rows = state.matches.filter(m =>
    (!q || [m.scholarship_name, m.country, m.provider].join(" ").toLowerCase().includes(q)) &&
    (!f.country || m.country === f.country) && (!f.status || m.deadline_status === f.status) &&
    (!f.funding || m.funding === f.funding) && (!f.verdict || m.verdict === f.verdict));
  if (f.sort === "deadline") rows = rows.sort((a, b) => (a.deadline_status === "closed") - (b.deadline_status === "closed") || (a.next_deadline || "9999").localeCompare(b.next_deadline || "9999"));
  if (f.sort === "name") rows = rows.sort((a, b) => a.scholarship_name.localeCompare(b.scholarship_name));
  return rows;
}

function renderVerdictChips() {
  const counts = v => state.matches.filter(m => !v || m.verdict === v).length;
  const chips = [["", "All"], ["eligible", "Eligible"], ["likely_eligible", "Likely eligible"], ["not_eligible", "Not eligible"]];
  $("#m-verdicts").innerHTML = chips.map(([v, l]) => `<button class="chip-btn ${state.filters.verdict === v ? "active" : ""}" data-v="${v}">${l} <span class="n">${counts(v)}</span></button>`).join("");
  $$("#m-verdicts .chip-btn").forEach(b => b.addEventListener("click", () => { state.filters.verdict = b.dataset.v; renderMatches(); }));
}

function matchCard(m, tracked) {
  const [vLabel, vIcon] = VERDICT[m.verdict];
  const sel = state.selected.has(m.scholarship_id);
  const closed = m.deadline_status === "closed";
  const deadline = m.next_deadline ? `${fmtDate(m.next_deadline)}${m.deadline_approximate ? " (approx.)" : ""}` : (m.deadline_text ? m.deadline_text[0].toUpperCase() + m.deadline_text.slice(1) : "Not available");
  return `<article class="s-card v-${m.verdict}${sel ? " selected" : ""}${closed ? " is-closed" : ""}" data-id="${esc(m.scholarship_id)}">
    <div class="s-top"><div class="row"><span class="chip">${icon("globe")}${esc(m.country)}</span><span class="badge b-${m.funding}">${m.funding === "full" ? "Fully funded" : "Partial"}</span></div>
      <span class="badge b-${esc(m.deadline_status || "closed")}">${statusLabel(m.deadline_status)}</span></div>
    <div><h3>${esc(m.scholarship_name)}</h3><div class="s-provider">${icon("building")}${esc(m.provider || "Provider not listed")}</div></div>
    <div class="s-fund">${icon("money")}<span>${m.funding_details ? esc(m.funding_details) : "Funding details not available. Check the official page."}</span></div>
    <div class="s-facts"><div><small>Deadline</small><b>${esc(deadline)}</b></div><div><small>Checked on</small><b>${m.checked ? esc(fmtDate(m.checked)) : "Not yet verified"}</b></div></div>
    <div class="row"><span class="badge b-${m.verdict}">${icon(vIcon)}${vLabel}</span></div>
    <p class="s-summary">${esc(m.summary)}</p>
    <div class="expander"><button type="button" aria-expanded="false">Why this verdict${m.deadline_note ? " · deadline details" : ""}${icon("chevron-down")}</button>
      <div class="expander-body"><div>
        <ul class="criteria">${m.criteria.map(c => `<li class="${c.status}">${icon(CRIT_ICON[c.status])}<span>${esc(c.detail)}</span></li>`).join("")}</ul>
        ${m.manual_checks.length ? `<div class="also"><strong>Also check yourself</strong><ul>${m.manual_checks.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
        ${m.deadline_note ? `<p class="muted" style="font-size:.82rem;margin:.6rem 0 0">${icon("clock")} ${esc(m.deadline_note)}</p>` : ""}
      </div></div></div>
    <div class="s-actions">
      <label class="select-toggle"><input type="checkbox" ${sel ? "checked" : ""} aria-label="Select for plan">Select</label>
      <button class="btn btn-ghost btn-sm track" ${tracked ? "disabled" : ""}>${icon(tracked ? "check" : "bookmark")}${tracked ? "Tracked" : "Track"}</button>
      <a class="btn btn-ghost btn-sm" href="${esc(m.official_url)}" target="_blank" rel="noopener noreferrer">${icon("external")}Official page</a>
    </div></article>`;
}

function renderMatches() {
  renderVerdictChips();
  const list = $("#matches-list");
  const tracked = new Set(state.apps.map(a => a.scholarship_id));
  const rows = filteredMatches();
  if (!state.matches.length) { list.innerHTML = emptyState("empty-matches.jpg", "No scholarships yet", "There are no scholarships in the catalog for your level."); return; }
  if (!rows.length) {
    list.innerHTML = emptyState("empty-matches.jpg", "No matches for these filters", "Try clearing a filter or searching for something else.", `<button class="btn btn-ghost" id="clear-filters">Clear filters</button>`);
    $("#clear-filters").addEventListener("click", clearFilters);
    return;
  }
  list.innerHTML = rows.map(m => matchCard(m, tracked.has(m.scholarship_id))).join("");
  $$(".s-card", list).forEach(card => {
    const id = card.dataset.id;
    const exp = $(".expander", card);
    $("button", exp).addEventListener("click", () => { const open = exp.classList.toggle("open"); $("button", exp).setAttribute("aria-expanded", open); });
    $(".select-toggle input", card).addEventListener("change", e => {
      e.target.checked ? state.selected.add(id) : state.selected.delete(id);
      card.classList.toggle("selected", e.target.checked);
      store(userKey("plan_ids"), JSON.stringify([...state.selected]));
      updateActionBar();
    });
    $(".track", card).addEventListener("click", async e => {
      const b = e.currentTarget;
      setLoading(b, true);
      try {
        await api(`/api/applications/${id}`, { method: "PUT", body: { status: "interested" } });
        state.apps.push({ scholarship_id: id, status: "interested" });
        b.innerHTML = `${icon("check")}Tracked`; b.classList.remove("loading");
        toast("Added to your tracker.", "success");
      } catch (err) { setLoading(b, false); toast(err.message, "error"); }
    });
  });
}

function updateActionBar() {
  const n = state.selected.size;
  $("#action-bar").classList.toggle("hidden", n === 0);
  $("#selected-count").textContent = `${n} selected`;
}
function clearFilters() {
  Object.assign(state.filters, { q: "", country: "", status: "", funding: "", verdict: "" });
  $("#m-search").value = ""; $("#m-country").value = ""; $("#m-status").value = ""; $("#m-funding").value = "";
  renderMatches();
}
let searchTimer;
$("#m-search").addEventListener("input", e => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.filters.q = e.target.value.trim(); renderMatches(); }, 150); });
[["#m-country", "country"], ["#m-status", "status"], ["#m-funding", "funding"], ["#m-sort", "sort"]].forEach(([sel, key]) =>
  $(sel).addEventListener("change", e => { state.filters[key] = e.target.value; renderMatches(); }));
$("#clear-selected").addEventListener("click", () => {
  state.selected.clear(); store(userKey("plan_ids"), "[]");
  $$(".s-card.selected").forEach(c => { c.classList.remove("selected"); $(".select-toggle input", c).checked = false; });
  updateActionBar();
});
$("#plan-selected").addEventListener("click", () => showView("plan"));

// ======================= plan =======================
// Ticks are saved only in this browser (the server doesn't store plan progress).
const planDone = () => loadJSON(userKey("plan_done"), {});
function setPlanDone(key, val) { const d = planDone(); val ? (d[key] = true) : delete d[key]; store(userKey("plan_done"), JSON.stringify(d)); }

async function renderPlan() {
  const out = $("#plan-output");
  if (!state.selected.size) {
    out.innerHTML = emptyState("empty-plan.jpg", "No plan yet", "Select one or more scholarships on the Matches page, then build a plan. We'll work back from each deadline.",
      `<button class="btn btn-primary" data-go="matches">${icon("search")}Choose scholarships</button>`);
    return;
  }
  out.innerHTML = `<div class="plan-cols"><div class="card">${skeletonLines(6)}</div><div class="card">${skeletonLines(6)}</div></div>`;
  let data;
  try { data = await api("/api/plan", { method: "POST", body: { scholarship_ids: [...state.selected] } }); }
  catch (e) { out.innerHTML = `<div class="alert alert-error">${esc(e.message)}</div>`; return; }
  const done = planDone();
  const key = e => `${e.scholarship_id}|${e.title}|${e.date}`;
  const tasks = data.merged_timeline.filter(e => e.status !== "deadline");
  const doneCount = tasks.filter(e => done[key(e)]).length;
  const pct = tasks.length ? Math.round((doneCount / tasks.length) * 100) : 0;
  const timeline = data.merged_timeline.map(e => {
    const isDeadline = e.status === "deadline", k = key(e), isDone = !isDeadline && done[k];
    const n = daysUntil(e.date);
    return `<li class="tl-item ${e.status}${isDone ? " done" : ""}" data-key="${esc(k)}">
      <button class="tl-dot" type="button" ${isDeadline ? "disabled aria-label=\"Deadline\"" : `aria-label="Mark ${esc(e.title)} as ${isDone ? "not done" : "done"}"`}>${icon(isDeadline ? "flag" : "check")}</button>
      <div class="tl-body"><span class="tl-date">${esc(fmtDate(e.date))} · ${relDays(n)}${e.status === "overdue" ? " · overdue" : ""}</span>
        <strong>${esc(e.title)}</strong><small>${esc(e.scholarship_name)}</small></div></li>`;
  }).join("");
  const cards = data.plans.map(p => {
    const [vLabel, vIcon] = VERDICT[p.verdict];
    const docs = p.documents.map(d => { const k = `doc|${p.scholarship_id}|${d}`; return `<li><label><input type="checkbox" data-key="${esc(k)}" ${done[k] ? "checked" : ""}><span>${esc(d)}</span></label></li>`; }).join("");
    return `<article class="card" data-sid="${esc(p.scholarship_id)}">
      <div class="card-title"><h3>${esc(p.scholarship_name)}</h3><span class="badge b-${p.verdict}">${icon(vIcon)}${vLabel}</span></div>
      <div class="s-facts" style="margin-bottom:.6rem"><div><small>Next deadline</small><b>${p.deadline ? esc(fmtDate(p.deadline)) : "Set by each course"}</b></div>
        <div><small>Apply</small><b><a href="${esc(p.apply_url)}" target="_blank" rel="noopener noreferrer">Official site ${icon("external")}</a></b></div></div>
      ${p.warnings.map(w => `<div class="warn-item">${icon("info")}<span>${esc(w)}</span></div>`).join("")}
      <div class="tabs" role="tablist"><button class="active" data-tab="gaps">Gaps (${p.gaps.length})</button><button data-tab="docs">Documents (${p.documents.length})</button><button data-tab="steps">Steps (${p.steps.length})</button></div>
      <div data-pane="gaps">${p.gaps.length ? p.gaps.map(g => `<div class="gap-item ${g.missing_info ? "info" : ""}">${icon(g.missing_info ? "help" : "alert")}<span>${esc(g.action)}</span></div>`).join("") : `<p class="muted">No gaps found by the eligibility engine.</p>`}</div>
      <div data-pane="docs" class="hidden"><ul class="checklist">${docs}</ul></div>
      <div data-pane="steps" class="hidden"><ol class="steps">${p.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol></div>
    </article>`;
  }).join("");
  out.innerHTML = `
    <div class="card plan-head"><div><h3 style="margin-bottom:.4rem" id="plan-progress-text">${data.plans.length} scholarship${data.plans.length > 1 ? "s" : ""} · ${doneCount} of ${tasks.length} tasks done</h3>
      <div class="progress"><i id="plan-progress" style="width:${pct}%"></i></div>
      <p class="local-note" style="margin:.5rem 0 0">${icon("info")}Ticks are saved in this browser only.</p></div>
      <button class="btn btn-ghost btn-sm" data-go="matches">${icon("edit")}Change selection</button></div>
    <div class="plan-cols">
      <div class="card"><div class="card-title"><h3>${icon("calendar")}Timeline</h3></div>
        ${data.merged_timeline.length ? `<ul class="timeline">${timeline}</ul>` : `<p class="muted">These scholarships have no single deadline, so there is no dated timeline. Use each card's steps and the course's own date.</p>`}</div>
      <div class="plan-cards">${cards}</div>
    </div>`;
  $$(".tl-item:not(.deadline) .tl-dot", out).forEach(dot => dot.addEventListener("click", () => {
    const li = dot.closest(".tl-item"), on = !li.classList.contains("done");
    setPlanDone(li.dataset.key, on);
    li.classList.toggle("done", on);
    dot.setAttribute("aria-label", `Mark ${$("strong", li).textContent} as ${on ? "not done" : "done"}`);
    const now = planDone(), n = tasks.filter(e => now[key(e)]).length;
    $("#plan-progress-text").textContent = `${data.plans.length} scholarship${data.plans.length > 1 ? "s" : ""} · ${n} of ${tasks.length} tasks done`;
    $("#plan-progress").style.width = `${tasks.length ? Math.round((n / tasks.length) * 100) : 0}%`;
    if (on && n === tasks.length) toast("All tasks done. Good luck with your applications!", "success");
  }));
  $$(".checklist input", out).forEach(cb => cb.addEventListener("change", () => setPlanDone(cb.dataset.key, cb.checked)));
  $$(".plan-cards .card", out).forEach(card => $$(".tabs button", card).forEach(t => t.addEventListener("click", () => {
    $$(".tabs button", card).forEach(b => b.classList.toggle("active", b === t));
    $$("[data-pane]", card).forEach(p => p.classList.toggle("hidden", p.dataset.pane !== t.dataset.tab));
  })));
}

// ======================= tracker =======================
const STATUSES = ["interested", "preparing", "applied", "interview", "accepted", "rejected", "withdrawn"];
const PIPELINE = ["interested", "preparing", "applied", "interview", "accepted"];
const cap = s => s[0].toUpperCase() + s.slice(1);

async function loadTracker() {
  const list = $("#tracker-list");
  list.innerHTML = skeletonCards(2);
  state.apps = await api("/api/applications");
  renderTracker();
}

function renderTracker() {
  const apps = state.apps, list = $("#tracker-list");
  const counts = s => apps.filter(a => !s || a.status === s).length;
  $("#t-filters").innerHTML = apps.length ? [["", "All"], ...STATUSES.filter(s => counts(s)).map(s => [s, cap(s)])]
    .map(([s, l]) => `<button class="chip-btn ${state.trackerFilter === s ? "active" : ""}" data-s="${s}">${l} <span class="n">${counts(s)}</span></button>`).join("") : "";
  $$("#t-filters .chip-btn").forEach(b => b.addEventListener("click", () => { state.trackerFilter = b.dataset.s; renderTracker(); }));
  if (!apps.length) {
    list.innerHTML = emptyState("empty-tracker.jpg", "Nothing tracked yet", "Press Track on a scholarship in Matches, or tell the assistant which ones you're going for.",
      `<button class="btn btn-primary" data-go="matches">${icon("search")}Browse matches</button>`);
    return;
  }
  const rows = apps.filter(a => !state.trackerFilter || a.status === state.trackerFilter);
  list.innerHTML = rows.map(a => {
    const stage = PIPELINE.indexOf(a.status);
    const n = daysUntil(a.next_action_date);
    const due = n === null ? "" : `<span class="due ${n < 0 ? "late" : n <= 7 ? "soon" : "ok"}">${n < 0 ? "Overdue · " : ""}${relDays(n)}</span>`;
    return `<article class="t-card" data-id="${esc(a.scholarship_id)}">
      <div class="t-head"><h3>${esc(a.scholarship_name || a.scholarship_id)}</h3>
        <select class="status-pill st-${esc(a.status)}" name="status" aria-label="Status">${STATUSES.map(s => `<option value="${s}" ${s === a.status ? "selected" : ""}>${cap(s)}</option>`).join("")}</select></div>
      ${stage >= 0 ? `<div class="stepper" aria-hidden="true">${PIPELINE.map((_, i) => `<span class="${i <= stage ? "on" : ""}"></span>`).join("")}</div>
        <div class="stepper-labels"><span>Interested</span><span>Applied</span><span>Accepted</span></div>` : ""}
      <div class="t-grid">
        <div class="field"><label>Next action</label><input name="next_action" value="${esc(a.next_action || "")}" placeholder="e.g. Book IELTS"></div>
        <div class="field"><label>Due ${due}</label><input name="next_action_date" type="date" value="${esc(a.next_action_date || "")}"></div>
      </div>
      <div class="field" style="margin-top:.7rem"><label>Notes</label><textarea name="notes" rows="2" placeholder="Anything to remember">${esc(a.notes || "")}</textarea></div>
      <div class="t-foot"><span class="history">${(a.history || []).map(h => `${esc(cap(h.status))} (${esc(fmtDate(h.date))})`).join(" → ")}</span>
        <div class="row">${a.official_url ? `<a class="btn btn-ghost btn-sm" href="${esc(a.official_url)}" target="_blank" rel="noopener noreferrer">${icon("external")}Official page</a>` : ""}
          <button class="btn btn-danger btn-sm remove">${icon("trash")}Remove</button><button class="btn btn-primary btn-sm save"><span class="btn-label">Save</span></button></div></div>
    </article>`;
  }).join("") || `<p class="muted">No applications with this status.</p>`;
  $$(".t-card", list).forEach(el => {
    const id = el.dataset.id;
    const pill = $("[name=status]", el);
    pill.addEventListener("change", () => { pill.className = `status-pill st-${pill.value}`; });
    $(".save", el).addEventListener("click", async e => {
      const btn = e.currentTarget;
      const body = { status: pill.value, next_action: $("[name=next_action]", el).value, next_action_date: $("[name=next_action_date]", el).value, notes: $("[name=notes]", el).value };
      setLoading(btn, true);
      try { await api(`/api/applications/${id}`, { method: "PUT", body }); toast("Application saved.", "success"); await loadTracker(); }
      catch (err) { toast(err.message, "error"); setLoading(btn, false); }
    });
    $(".remove", el).addEventListener("click", async () => {
      if (!await confirmDialog({ title: "Remove from tracker?", text: "Its status history and notes will be deleted.", ok: "Remove", danger: true, iconName: "trash" })) return;
      try { await api(`/api/applications/${id}`, { method: "DELETE" }); toast("Removed from tracker.", "success"); loadTracker(); }
      catch (err) { toast(err.message, "error"); }
    });
  });
}

// ======================= drafts =======================
async function refreshDraftCount() {
  try {
    const { drafts } = await api("/api/drafts");
    const n = drafts.filter(d => d.status === "pending").length;
    const badge = $("#draft-count");
    badge.textContent = n;
    badge.classList.toggle("hidden", n === 0);
  } catch (_) { /* not critical */ }
}

async function loadDrafts() {
  $("#drafts-list").innerHTML = skeletonLines(5);
  $("#draft-view").innerHTML = "";
  const { drafts, smtp_enabled } = await api("/api/drafts");
  state.drafts = drafts; state.smtp = smtp_enabled;
  if (!drafts.length) {
    $(".mail-layout").classList.remove("viewing");
    $("#drafts-list").innerHTML = emptyState("", "No drafts", "Ask the assistant to write to an admissions office.", "", true);
    $("#draft-view").innerHTML = emptyState("empty-drafts.jpg", "Your drafts appear here", "The assistant only writes drafts. Nothing is sent until you check and approve it.",
      `<button class="btn btn-primary" data-go="chat">${icon("sparkle")}Open the assistant</button>`);
    return;
  }
  if (!drafts.some(d => d.id === state.activeDraft)) state.activeDraft = (drafts.find(d => d.status === "pending") || drafts[0]).id;
  renderDraftList();
  renderDraftView();
}

function renderDraftList() {
  $("#drafts-list").innerHTML = state.drafts.map(d => `<button class="mail-item ${d.id === state.activeDraft ? "active" : ""}" data-id="${d.id}">
    <span class="mi-top"><strong>${esc(d.to_office)}</strong><time>${esc(fmtDate(d.created_at))}</time></span>
    <span class="mi-sub">${esc(d.subject)}</span>
    <span class="mi-top"><span class="mi-prev">${esc(d.body.slice(0, 80))}</span><span class="badge d-${esc(d.status)}">${cap(d.status)}</span></span></button>`).join("");
  $$("#drafts-list .mail-item").forEach(b => b.addEventListener("click", () => {
    state.activeDraft = Number(b.dataset.id);
    renderDraftList(); renderDraftView();
    $(".mail-layout").classList.add("viewing");
  }));
}

function renderDraftView() {
  const d = state.drafts.find(x => x.id === state.activeDraft);
  const view = $("#draft-view");
  if (!d) { view.innerHTML = ""; return; }
  const back = `<button class="btn btn-ghost btn-sm mv-back" id="mv-back">${icon("chevron-left")}All drafts</button>`;
  if (d.status === "pending") {
    view.innerHTML = `${back}<div class="mv-head"><div><span class="badge d-pending">Waiting for your approval</span><h3 style="margin-top:.5rem">To: ${esc(d.to_office)}</h3>
        <small class="muted">Created ${esc(fmtDate(d.created_at))}</small></div></div>
      <div class="field"><label>Recipient email</label><input name="to_address" type="email" value="${esc(d.to_address || "")}" placeholder="Copy it from the official page"></div>
      <div class="field"><label>Subject</label><input name="subject" value="${esc(d.subject)}"></div>
      <div class="field" style="flex:1"><label>Message</label><textarea name="body">${esc(d.body)}</textarea></div>
      <div class="alert alert-info">${state.smtp ? "Approving sends this email from the configured account." : "Approving opens it in your own email app, so you send it yourself."} Check the recipient on the official page first.</div>
      <div class="mv-actions"><button class="btn btn-danger discard">${icon("trash")}Discard</button><button class="btn btn-ghost save"><span class="btn-label">Save edits</span></button>
        <button class="btn btn-primary approve">${icon("send")}${state.smtp ? "Approve and send" : "Approve and open email app"}</button></div>`;
    const edits = () => ({ to_address: $("[name=to_address]", view).value, subject: $("[name=subject]", view).value, body: $("[name=body]", view).value });
    $(".save", view).addEventListener("click", async e => {
      setLoading(e.currentTarget, true);
      try { await api(`/api/drafts/${d.id}`, { method: "PUT", body: edits() }); Object.assign(d, edits()); renderDraftList(); toast("Draft saved.", "success"); }
      catch (err) { toast(err.message, "error"); } finally { setLoading($(".save", view), false); }
    });
    $(".discard", view).addEventListener("click", async () => {
      if (!await confirmDialog({ title: "Discard this draft?", text: "It won't be sent.", ok: "Discard", danger: true, iconName: "trash" })) return;
      try { await api(`/api/drafts/${d.id}/discard`, { method: "POST" }); toast("Draft discarded.", "success"); loadDrafts(); refreshDraftCount(); }
      catch (err) { toast(err.message, "error"); }
    });
    $(".approve", view).addEventListener("click", async () => {
      if (!await confirmDialog({ title: "Approve this email?", text: "You confirm the recipient and content are correct.", ok: "Approve", iconName: "send" })) return;
      try {
        await api(`/api/drafts/${d.id}`, { method: "PUT", body: edits() });
        const r = await api(`/api/drafts/${d.id}/approve`, { method: "POST" });
        if (r.status === "sent") toast("Email sent.", "success"); else { toast("Approved. Opening your email app…", "success"); window.location.href = r.mailto; }
        loadDrafts(); refreshDraftCount();
      } catch (err) { toast(err.message, "error"); }
    });
  } else {
    view.innerHTML = `${back}<div class="mv-head"><div><span class="badge d-${esc(d.status)}">${cap(d.status)}</span><h3 style="margin-top:.5rem">${esc(d.subject)}</h3>
        <small class="muted">To ${esc(d.to_office)}${d.to_address ? ` &lt;${esc(d.to_address)}&gt;` : ""} · created ${esc(fmtDate(d.created_at))}${d.approved_at ? ` · approved ${esc(fmtDate(d.approved_at))}` : ""}</small></div></div>
      <div class="mail-body-read">${esc(d.body)}</div>
      ${d.status === "approved" ? `<div class="mv-actions"><a class="btn btn-primary" href="${esc(d.mailto)}">${icon("mail")}Open in my email app</a></div>` : ""}`;
  }
  $("#mv-back")?.addEventListener("click", () => $(".mail-layout").classList.remove("viewing"));
}

// ======================= scam check =======================
function scamIntro() {
  $("#scam-output").innerHTML = `<div class="card">${emptyState("empty-scam.jpg", "Check before you trust", "Real scholarships never charge a processing fee, never ask for your bank PIN or OTP, and always link to an official website.")}</div>`;
}
scamIntro();
$("#scam-clear").addEventListener("click", () => { $("#scam-input").value = ""; scamIntro(); $("#scam-input").focus(); });
$("#scam-paste").addEventListener("click", async () => {
  try { $("#scam-input").value = await navigator.clipboard.readText(); $("#scam-input").focus(); }
  catch (_) { toast("Your browser blocked pasting. Press Ctrl+V in the box instead.", "info"); $("#scam-input").focus(); }
});
function gaugeSvg(score) {
  const len = Math.PI * 70;
  return `<svg class="gauge" viewBox="0 0 170 100" role="img" aria-label="Risk score ${score} out of 100">
    <path class="g-bg" d="M15 88 A70 70 0 0 1 155 88"/><path class="g-fg" d="M15 88 A70 70 0 0 1 155 88" stroke-dasharray="${len}" stroke-dashoffset="${len}" data-target="${len * (1 - score / 100)}"/>
    <text x="85" y="78">${score}</text><text class="sub" x="85" y="95">out of 100</text></svg>`;
}
$("#scam-form").addEventListener("submit", async e => {
  e.preventDefault();
  const text = $("#scam-input").value.trim();
  if (!text) { toast("Paste a message or link first.", "info"); return; }
  const btn = $("button[type=submit]", e.target), out = $("#scam-output");
  setLoading(btn, true);
  out.innerHTML = `<div class="card">${skeletonLines(5)}</div>`;
  try {
    const r = await api("/api/scam-check", { method: "POST", body: { text } });
    const label = { low: "Low risk", medium: "Suspicious", high: "High risk: likely a scam" }[r.risk_level];
    const blurb = { low: "We found no common scam signs. Still confirm everything on the official page.", medium: "Some warning signs. Don't pay or share documents until you confirm on the official site.", high: "Several strong scam signs. Do not pay, and do not share personal documents or codes." }[r.risk_level];
    out.innerHTML = `<div class="card risk-${r.risk_level}"><div class="gauge-card">${gaugeSvg(r.score)}<div><p class="risk-title">${label}</p><p class="muted" style="margin:0">${blurb}</p></div></div></div>
      ${r.signals.length ? `<h3 style="margin:.3rem 0 0">Warning signs (${r.signals.length})</h3>${r.signals.map((s, i) => `<div class="signal${s.weight >= 30 ? " heavy" : ""}" style="animation-delay:${i * 60}ms">${icon("alert")}<div>${esc(s.explanation)}${s.evidence ? `<code>${esc(s.evidence)}</code>` : ""}</div></div>`).join("")}` : ""}
      ${r.trusted_links.length ? `<div class="card"><div class="card-title"><h3>${icon("check-circle")}Official links found</h3></div>${r.trusted_links.map(l => `<div><a href="${esc(l)}" target="_blank" rel="noopener noreferrer">${esc(l)}</a></div>`).join("")}</div>` : ""}
      ${r.advice.length ? `<div class="card"><div class="card-title"><h3>${icon("shield")}What to do</h3></div><ul class="advice">${r.advice.map(a => `<li>${esc(a)}</li>`).join("")}</ul></div>` : ""}`;
    requestAnimationFrame(() => $$(".gauge .g-fg", out).forEach(p => { p.style.strokeDashoffset = p.dataset.target; }));
  } catch (err) { out.innerHTML = `<div class="alert alert-error">${esc(err.message)}</div>`; }
  finally { setLoading(btn, false); }
});

// ======================= profile =======================
// [name, label, type, hint, step/options, min, max]
const PROFILE_SECTIONS = [
  ["Personal information", "user", "Used for nationality and age limits.", [
    ["nationality", "Nationality", "text", "e.g. Pakistan"], ["date_of_birth", "Date of birth", "date"], ["city", "City", "text"]]],
  ["Academic information", "cap", "Your latest qualification and results.", [
    ["current_education", "Current / last qualification", "text", "e.g. FSc Pre-Engineering, BS Computer Science"], ["field_of_study", "Field studied", "text"],
    ["grade_percent", "HSSC / A-Level percentage", "number", "For bachelor's applicants", "0.1", 0, 100], ["cgpa", "CGPA", "number", "For master's applicants", "0.01", 0, 5],
    ["cgpa_scale", "CGPA scale", "number", "Usually 4", "0.1", 1, 5], ["graduation_year", "Graduation year", "number", "", "1", 1980, 2040]]],
  ["Language proficiency", "globe", "Add any test you've taken. Leave the rest empty.", [
    ["ielts", "IELTS overall", "number", "0 to 9", "0.5", 0, 9], ["toefl_ibt", "TOEFL iBT", "number", "0 to 120", "1", 0, 120], ["pte", "PTE Academic", "number", "10 to 90", "1", 10, 90],
    ["duolingo", "Duolingo English Test", "number", "10 to 160", "1", 10, 160], ["sat", "SAT", "number", "400 to 1600", "1", 400, 1600], ["gre", "GRE", "number", "260 to 340", "1", 260, 340],
    ["jlpt", "JLPT level", "select", "", ["", "N1", "N2", "N3", "N4", "N5"]]]],
  ["Work experience", "building", "Full-time work after your degree.", [
    ["work_experience_months", "Work experience (months)", "number", "e.g. 24 for two years", "1", 0, 600]]],
  ["Study preferences", "compass", "Helps the assistant focus on what you want.", [
    ["intended_field", "Field you want to study", "text"], ["preferred_countries", "Preferred countries", "text", "Comma-separated, e.g. Germany, Japan"],
    ["financial_need", "Need financial support to study abroad?", "select", "", ["", "yes", "no"]]]],
];
let profileSnapshot = "";

async function loadProfile() {
  const form = $("#profile-form");
  form.innerHTML = `<div class="card">${skeletonLines(6)}</div>`;
  const p = await api("/api/profile");
  state.profile = p;
  form.innerHTML = PROFILE_SECTIONS.map(([title, ic, sub, fields]) => `<section class="card p-section"><h3>${icon(ic)}${title}</h3><p class="muted">${sub}</p><div class="p-grid">${fields.map(f => {
    const [name, label, type, hint, extra, min, max] = f;
    let value = p[name];
    if (name === "preferred_countries") value = (value || []).join(", ");
    if (name === "financial_need") value = value === true ? "yes" : value === false ? "no" : "";
    const lab = `<label for="pf-${name}">${label}${hint && type !== "select" ? ` <span class="hint">· ${esc(hint)}</span>` : ""}</label>`;
    if (type === "select") return `<div class="field">${lab}<select id="pf-${name}" name="${name}">${extra.map(o => `<option value="${o}" ${String(value ?? "") === o ? "selected" : ""}>${o ? cap(o) : "Not set"}</option>`).join("")}</select></div>`;
    return `<div class="field">${lab}<input id="pf-${name}" name="${name}" type="${type}" ${extra ? `step="${extra}"` : ""} ${min !== undefined ? `min="${min}" max="${max}"` : ""} value="${esc(value ?? "")}"><small class="field-error"></small></div>`;
  }).join("")}</div></section>`).join("") +
    `<div class="save-bar"><span class="clean" id="save-state">All changes saved</span><button class="btn btn-primary" type="submit"><span class="btn-label">Save changes</span></button></div>`;
  profileSnapshot = new URLSearchParams(new FormData(form)).toString();
  $("#profile-notes").innerHTML = p.notes && p.notes.length ? `<ul class="notes-list">${p.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>`
    : `<p class="muted" style="margin:0;font-size:.86rem">Nothing yet. Things you tell the assistant (like plans or constraints) show up here.</p>`;
  $("#profile-strength").innerHTML = strengthHtml(p);
  animateRings($("#profile-strength"));
  loadEpisodes();
}
$("#profile-form").addEventListener("input", e => {
  if (e.target.matches("input")) fieldError(e.target, "");
  const dirty = new URLSearchParams(new FormData($("#profile-form"))).toString() !== profileSnapshot;
  const s = $("#save-state");
  if (s) { s.textContent = dirty ? "You have unsaved changes" : "All changes saved"; s.className = dirty ? "dirty" : "clean"; }
});

async function loadEpisodes() {
  const box = $("#episodes-list");
  box.innerHTML = skeletonLines(3);
  const { episodes } = await api("/api/memory");
  box.innerHTML = episodes.length ? episodes.map(e => `<div class="episode"><div class="meta">${esc(fmtDate(e.date) || e.date)}</div><p>${esc(e.summary)}</p>
      ${e.decisions.length ? `<div class="meta"><strong>Decided:</strong> ${e.decisions.map(esc).join("; ")}</div>` : ""}
      ${e.next_steps.length ? `<div class="meta"><strong>To do:</strong> ${e.next_steps.map(esc).join("; ")}</div>` : ""}</div>`).join("")
    : `<p class="muted" style="margin:0;font-size:.86rem">No past sessions yet.</p>`;
}
$("#refresh-memory").addEventListener("click", async e => {
  const btn = e.currentTarget;
  setLoading(btn, true);
  try { const r = await api("/api/memory/refresh", { method: "POST" }); toast(`Updated ${r.summarized} session summar${r.summarized === 1 ? "y" : "ies"}.`, "success"); await loadEpisodes(); }
  catch (err) { toast(err.message, "error"); } finally { setLoading(btn, false); }
});

$("#profile-form").addEventListener("submit", async e => {
  e.preventDefault();
  const form = e.target;
  let ok = true;
  $$("input[type=number]", form).forEach(i => {
    const v = i.value;
    const bad = v !== "" && (Number.isNaN(Number(v)) || (i.min !== "" && Number(v) < Number(i.min)) || (i.max !== "" && Number(v) > Number(i.max)));
    fieldError(i, bad ? `Enter a value from ${i.min} to ${i.max}.` : "");
    if (bad) ok = false;
  });
  const cgpa = $("[name=cgpa]", form), scale = $("[name=cgpa_scale]", form);
  if (cgpa.value && scale.value && Number(cgpa.value) > Number(scale.value)) { fieldError(cgpa, "CGPA can't be higher than the scale."); ok = false; }
  if (!ok) { toast("Please fix the highlighted fields.", "error"); $(".field.invalid input", form)?.focus(); return; }
  const updates = {};
  for (const [k, v] of new FormData(form)) {
    if (k === "financial_need") updates[k] = v === "" ? null : v === "yes";
    else if (k === "preferred_countries") updates[k] = v.split(",").map(s => s.trim()).filter(Boolean);
    else updates[k] = v === "" ? null : v;
  }
  if (!updates.preferred_countries?.length) updates.preferred_countries = null;
  const btn = $("button[type=submit]", form);
  setLoading(btn, true);
  try {
    const p = await api("/api/profile", { method: "PUT", body: { updates } });
    state.profile = p;
    profileSnapshot = new URLSearchParams(new FormData(form)).toString();
    $("#save-state").textContent = "All changes saved"; $("#save-state").className = "clean";
    $("#profile-strength").innerHTML = strengthHtml(p); animateRings($("#profile-strength"));
    toast("Profile saved. Your matches are updated.", "success");
  } catch (err) { toast(err.message, "error"); }
  finally { setLoading(btn, false); }
});

// ======================= review (admin) =======================
$$("#r-tabs .seg-btn").forEach(b => b.addEventListener("click", () => {
  $$("#r-tabs .seg-btn").forEach(x => x.classList.toggle("active", x === b));
  moveSeg($("#r-tabs"));
  state.reviewStatus = b.dataset.status;
  loadReviews();
}));

function diffHtml(diff) {
  const lines = String(diff || "").split("\n");
  const removed = [], added = [];
  const full = lines.map(l => {
    if (l.startsWith("+++") || l.startsWith("---")) return `<div class="hunk">${esc(l)}</div>`;
    if (l.startsWith("@@")) return `<div class="hunk">${esc(l)}</div>`;
    if (l.startsWith("+")) { added.push(l.slice(1)); return `<div class="add">${esc(l)}</div>`; }
    if (l.startsWith("-")) { removed.push(l.slice(1)); return `<div class="del">${esc(l)}</div>`; }
    return `<div>${esc(l)}</div>`;
  }).join("");
  const col = (arr, cls) => arr.length ? `<div class="diff">${arr.slice(0, 60).map(x => `<div class="${cls}">${esc(x)}</div>`).join("")}</div>` : `<p class="muted" style="font-size:.84rem">Nothing.</p>`;
  return `<div class="diff-cols"><div><h4>Previous (removed)</h4>${col(removed, "del")}</div><div><h4>Updated (added)</h4>${col(added, "add")}</div></div>
    <div class="expander"><button type="button">Full page diff${icon("chevron-down")}</button><div class="expander-body"><div><div class="diff" style="margin-top:.5rem">${full}</div></div></div></div>`;
}

async function loadReviews() {
  moveSeg($("#r-tabs"));
  const list = $("#review-list");
  list.innerHTML = skeletonCards(2);
  const rows = await api(`/api/admin/reviews?status=${state.reviewStatus}`);
  if (!rows.length) {
    list.innerHTML = `<div class="card">${emptyState("", state.reviewStatus === "pending" ? "No pending changes" : `No ${state.reviewStatus} reviews`,
      state.reviewStatus === "pending" ? "Run the monitor to check the official pages for changes." : "Reviews appear here after a decision.", "", true)}</div>`;
    return;
  }
  const pending = state.reviewStatus === "pending";
  list.innerHTML = rows.map(r => `<article class="r-card" data-id="${r.id}">
    <div class="r-head"><div><h3>${esc(r.scholarship_id)}</h3><small class="muted">Found ${esc(fmtDate(r.created_at))} · <a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.url)}</a></small></div>
      <span class="badge ${pending ? "b-likely_eligible" : r.status === "approved" ? "b-eligible" : "b-closed"}">${cap(r.status)}</span></div>
    ${r.summary ? `<div class="alert alert-info">${esc(r.summary)}</div>` : ""}
    ${diffHtml(r.diff)}
    ${pending ? `<div class="field"><label>Catalog changes to apply <span class="hint">· JSON: dotted path → new value</span></label><textarea name="patch" rows="4">${esc(JSON.stringify(r.proposed_patch || {}, null, 2))}</textarea><small class="field-error"></small></div>
      <div class="field"><label>Note (optional)</label><input name="note" placeholder="What you checked"></div>
      <div class="row" style="justify-content:flex-end"><button class="btn btn-danger reject">${icon("x")}Reject</button><button class="btn btn-primary approve">${icon("check")}Approve and mark verified</button></div>` : ""}
  </article>`).join("");
  $$(".r-card", list).forEach(el => {
    const id = el.dataset.id;
    $$(".expander > button", el).forEach(b => b.addEventListener("click", () => b.parentElement.classList.toggle("open")));
    if (!pending) return;
    const note = () => $("[name=note]", el).value || null;
    $(".approve", el).addEventListener("click", async () => {
      let patch;
      const ta = $("[name=patch]", el);
      try { patch = JSON.parse(ta.value || "{}"); fieldError(ta, ""); } catch (_) { fieldError(ta, "This is not valid JSON."); return; }
      if (!await confirmDialog({ title: "Approve this change?", text: "The catalog will be updated and marked verified today.", ok: "Approve", iconName: "check-circle" })) return;
      try { await api(`/api/admin/reviews/${id}/approve`, { method: "POST", body: { patch, note: note() } }); toast("Change approved.", "success"); loadReviews(); }
      catch (e) { toast(e.message, "error"); }
    });
    $(".reject", el).addEventListener("click", async () => {
      if (!await confirmDialog({ title: "Reject this change?", text: "The catalog stays as it is.", ok: "Reject", danger: true, iconName: "x-circle" })) return;
      try { await api(`/api/admin/reviews/${id}/reject`, { method: "POST", body: { note: note() } }); toast("Change rejected.", "success"); loadReviews(); }
      catch (e) { toast(e.message, "error"); }
    });
  });
}

$("#run-monitor").addEventListener("click", async e => {
  const btn = e.currentTarget;
  setLoading(btn, true);
  toast("Checking official pages. This can take a minute.", "info");
  try { const r = await api("/api/admin/monitor/run", { method: "POST" }); toast(`Done: ${r.changed} changed, ${r.baseline} new, ${r.unchanged} unchanged, ${r.error} errors.`, "success"); loadReviews(); }
  catch (err) { toast(err.message, "error"); }
  finally { setLoading(btn, false); }
});

// ======================= boot =======================
hydrateIcons();
(async function boot() {
  requestAnimationFrame(() => $$(".seg").forEach(moveSeg));
  const token = load("sp_token");
  if (!token) return showAuth();
  state.token = token;
  try { state.user = await api("/api/me"); startApp(); }
  catch (_) { logout(); }
})();
