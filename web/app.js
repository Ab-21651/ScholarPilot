"use strict";

// ---------- helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const state = { token: null, user: null, conversationId: null, selected: new Set(), matches: [] };

function store(key, value) {
  try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch (_) { /* storage blocked */ }
}
function load(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), 3500);
}

async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && state.token) { logout(); throw new Error("Session expired. Please log in again."); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(d => d.msg).join("; ") : data.detail;
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data;
}

// Small, safe markdown renderer for assistant replies (escape first, then format).
function renderMarkdown(src) {
  const inline = s => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  const lines = src.split("\n");
  let html = "", list = null, para = [];
  const flushPara = () => { if (para.length) { html += `<p>${para.map(inline).join("<br>")}</p>`; para = []; } };
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
      flushPara(); if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${inline(m[1])}</li>`;
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      flushPara(); if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${inline(m[1])}</li>`;
    } else if ((m = line.match(/^#{1,6}\s+(.*)/))) {
      flushPara(); closeList(); html += `<h4>${inline(m[1])}</h4>`;
    } else if (!line.trim()) {
      flushPara(); closeList();
    } else {
      closeList(); para.push(line);
    }
  }
  flushPara(); closeList();
  return html;
}

// ---------- auth ----------
function showAuth() {
  $("#auth-view").classList.remove("hidden");
  $("#app-view").classList.add("hidden");
}

function logout() {
  state.token = null; state.user = null; state.conversationId = null;
  store("sp_token", null);
  showAuth();
}

$$("[data-auth]").forEach(btn => btn.addEventListener("click", () => {
  $$("[data-auth]").forEach(b => b.classList.toggle("active", b === btn));
  $("#login-form").classList.toggle("hidden", btn.dataset.auth !== "login");
  $("#signup-form").classList.toggle("hidden", btn.dataset.auth !== "signup");
  $("#auth-error").textContent = "";
}));

async function handleAuth(form, path) {
  $("#auth-error").textContent = "";
  const body = Object.fromEntries(new FormData(form));
  try {
    const data = await api(path, { method: "POST", body });
    state.token = data.token; state.user = data.user;
    store("sp_token", data.token);
    startApp();
  } catch (e) { $("#auth-error").textContent = e.message; }
}
$("#login-form").addEventListener("submit", e => { e.preventDefault(); handleAuth(e.target, "/api/auth/login"); });
$("#signup-form").addEventListener("submit", e => { e.preventDefault(); handleAuth(e.target, "/api/auth/signup"); });
$("#logout").addEventListener("click", logout);

// ---------- navigation ----------
const loaders = { chat: loadConversations, matches: loadMatches, drafts: loadDrafts, profile: loadProfile, review: loadReviews };

function showView(name) {
  $$("#nav button").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  $$("[data-panel]").forEach(p => p.classList.toggle("hidden", p.dataset.panel !== name));
  if (loaders[name]) loaders[name]().catch(e => toast(e.message));
}
$$("#nav button").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));

async function startApp() {
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  $("#who-name").textContent = state.user.full_name;
  $("#who-level").textContent = state.user.target_level === "bachelor" ? "Bachelor's" : "Master's";
  $("#nav-review").classList.toggle("hidden", !state.user.is_admin);
  showView("chat");
  refreshDraftCount();
}

// ---------- chat ----------
const SUGGESTIONS = {
  bachelor: ["Which scholarships can I get for a bachelor's?", "Am I eligible for MEXT?", "What do I need for Stipendium Hungaricum?"],
  master: ["Which fully funded master's scholarships fit me?", "Am I eligible for Chevening?", "Make me a timeline for DAAD EPOS"],
};

async function loadConversations() {
  const convos = await api("/api/conversations");
  const ul = $("#convo-list");
  ul.innerHTML = convos.map(c => `<li data-id="${c.id}" class="${c.id === state.conversationId ? "active" : ""}">
      <button class="title" title="${esc(c.title)}">${esc(c.title)}</button>
      <button class="del" aria-label="Delete chat" title="Delete">×</button></li>`).join("");
  $$("li", ul).forEach(li => {
    $(".title", li).addEventListener("click", () => openConversation(Number(li.dataset.id)));
    $(".del", li).addEventListener("click", async () => {
      if (!confirm("Delete this chat?")) return;
      await api(`/api/conversations/${li.dataset.id}`, { method: "DELETE" });
      if (Number(li.dataset.id) === state.conversationId) state.conversationId = null;
      loadConversations();
    });
  });
  if (state.conversationId === null) {
    if (convos.length) await openConversation(convos[0].id); else renderMessages([]);
  }
}

async function openConversation(id) {
  state.conversationId = id;
  $$("#convo-list li").forEach(li => li.classList.toggle("active", Number(li.dataset.id) === id));
  const c = await api(`/api/conversations/${id}`);
  renderMessages(c.messages);
}

function messageHtml(m) {
  if (m.role === "user") return `<div class="msg user">${esc(m.text)}</div>`;
  const tools = m.tools && m.tools.length ? `<div class="tools-used">Used: ${[...new Set(m.tools)].map(t => t.replace(/_/g, " ")).join(", ")}</div>` : "";
  return `<div class="msg assistant">${renderMarkdown(m.text)}${tools}</div>`;
}

function renderMessages(messages) {
  const box = $("#messages");
  if (!messages.length) {
    box.innerHTML = `<div class="empty-chat"><h3>Assalam-o-Alaikum, ${esc(state.user.full_name.split(" ")[0])}!</h3>
      <p>Tell me about your grades, tests and plans, and I'll find funded scholarships you qualify for, explain why, and build a timeline.</p></div>`;
  } else {
    box.innerHTML = messages.map(messageHtml).join("");
  }
  $("#suggestions").innerHTML = messages.length ? "" : SUGGESTIONS[state.user.target_level]
    .map(s => `<button type="button">${esc(s)}</button>`).join("");
  $$("#suggestions button").forEach(b => b.addEventListener("click", () => { $("#chat-input").value = b.textContent; $("#chat-form").requestSubmit(); }));
  box.scrollTop = box.scrollHeight;
}

$("#new-chat").addEventListener("click", async () => {
  const c = await api("/api/conversations", { method: "POST" });
  state.conversationId = c.id;
  await loadConversations();
  renderMessages([]);
  $("#chat-input").focus();
});

$("#chat-input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});

$("#chat-form").addEventListener("submit", async e => {
  e.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  const box = $("#messages");
  if (state.conversationId === null) {
    const c = await api("/api/conversations", { method: "POST" });
    state.conversationId = c.id;
  }
  if ($(".empty-chat", box)) box.innerHTML = "";
  $("#suggestions").innerHTML = "";
  box.insertAdjacentHTML("beforeend", messageHtml({ role: "user", text }));
  box.insertAdjacentHTML("beforeend", `<div class="thinking">Checking official sources</div>`);
  box.scrollTop = box.scrollHeight;
  input.value = "";
  $("#send-btn").disabled = true;
  try {
    const r = await api(`/api/conversations/${state.conversationId}/messages`, { method: "POST", body: { text } });
    $(".thinking", box)?.remove();
    box.insertAdjacentHTML("beforeend", messageHtml({ role: "assistant", text: r.reply, tools: r.tool_calls.map(t => t.tool) }));
    if (r.created_drafts.length) { toast("New email draft waiting for your approval in Drafts."); refreshDraftCount(); }
    loadConversations();
  } catch (err) {
    $(".thinking", box)?.remove();
    box.insertAdjacentHTML("beforeend", `<div class="msg assistant error">${esc(err.message)}</div>`);
  } finally {
    $("#send-btn").disabled = false;
    box.scrollTop = box.scrollHeight;
  }
});

// ---------- matches ----------
const VERDICT_TEXT = { eligible: "Eligible", likely_eligible: "Needs info", not_eligible: "Not yet" };
const ICON = { pass: "✓", fail: "✗", unknown: "?" };

async function loadMatches() {
  const list = $("#matches-list");
  list.innerHTML = `<p class="muted">Checking...</p>`;
  state.matches = await api("/api/matches");
  list.innerHTML = state.matches.map(r => `
    <article class="card">
      <div class="card-head">
        <label><input type="checkbox" data-id="${esc(r.scholarship_id)}" ${state.selected.has(r.scholarship_id) ? "checked" : ""}> ${esc(r.scholarship_name)}</label>
        <span class="verdict ${r.verdict}">${VERDICT_TEXT[r.verdict]}</span>
      </div>
      <div class="meta">${esc(r.country)} · ${r.funding === "full" ? "Fully funded" : "Partial funding"} · <a href="${esc(r.official_url)}" target="_blank" rel="noopener noreferrer">Official page</a></div>
      ${r.catalog_status !== "verified" ? `<div class="unverified">Details not yet re-verified by a reviewer. Confirm on the official page.</div>` : ""}
      <ul class="criteria">${r.criteria.map(c => `<li><span class="${c.status}">${ICON[c.status]}</span><span>${esc(c.detail)}</span></li>`).join("")}</ul>
      ${r.manual_checks.length ? `<details><summary>Also check yourself (${r.manual_checks.length})</summary><ul>${r.manual_checks.map(m => `<li>${esc(m)}</li>`).join("")}</ul></details>` : ""}
    </article>`).join("") || `<p class="muted">No scholarships in the catalog for your level yet.</p>`;
  $$("input[type=checkbox]", list).forEach(cb => cb.addEventListener("change", () => {
    cb.checked ? state.selected.add(cb.dataset.id) : state.selected.delete(cb.dataset.id);
    $("#plan-selected").disabled = state.selected.size === 0;
  }));
  $("#plan-selected").disabled = state.selected.size === 0;
}

$("#plan-selected").addEventListener("click", () => { showView("plan"); buildPlan(); });

// ---------- plan ----------
async function buildPlan() {
  const out = $("#plan-output");
  if (!state.selected.size) { out.innerHTML = `<p class="muted">Nothing selected yet.</p>`; return; }
  out.innerHTML = `<p class="muted">Building your plan...</p>`;
  try {
    const data = await api("/api/plan", { method: "POST", body: { scholarship_ids: [...state.selected] } });
    const timeline = data.merged_timeline.map(e => `
      <li class="${e.status}"><span class="date">${esc(e.date)}</span>${esc(e.title)}
        <div class="tag">${esc(e.scholarship_name)}${e.status === "overdue" ? " · overdue" : ""}</div></li>`).join("");
    const cards = data.plans.map(p => `
      <article class="card">
        <div class="card-head"><h3>${esc(p.scholarship_name)}</h3><span class="verdict ${p.verdict}">${VERDICT_TEXT[p.verdict]}</span></div>
        <div class="meta">Next deadline: <strong>${esc(p.deadline || "unknown")}</strong> · <a href="${esc(p.apply_url)}" target="_blank" rel="noopener noreferrer">Apply on the official site</a></div>
        ${p.warnings.map(w => `<div class="warn">${esc(w)}</div>`).join("")}
        ${p.gaps.length ? `<h4>Your gaps</h4>${p.gaps.map(g => `<div class="gap ${g.missing_info ? "info" : ""}">${esc(g.action)}</div>`).join("")}` : `<div class="meta">No gaps found by the engine.</div>`}
        <details open><summary>Documents (${p.documents.length})</summary><ul>${p.documents.map(d => `<li>${esc(d)}</li>`).join("")}</ul></details>
        <details><summary>Steps (${p.steps.length})</summary><ul>${p.steps.map(d => `<li>${esc(d)}</li>`).join("")}</ul></details>
      </article>`).join("");
    out.innerHTML = `<h3>Timeline</h3><ul class="timeline">${timeline}</ul><div class="plan-grid">${cards}</div>`;
  } catch (e) { out.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
}

// ---------- drafts ----------
async function refreshDraftCount() {
  try {
    const { drafts } = await api("/api/drafts");
    const n = drafts.filter(d => d.status === "pending").length;
    const badge = $("#draft-count");
    badge.textContent = n;
    badge.classList.toggle("hidden", n === 0);
  } catch (_) { /* ignore */ }
}

async function loadDrafts() {
  const { drafts, smtp_enabled } = await api("/api/drafts");
  const list = $("#drafts-list");
  if (!drafts.length) { list.innerHTML = `<p class="muted">No drafts yet. Ask the assistant to write to an admissions office.</p>`; return; }
  list.innerHTML = drafts.map(d => `
    <article class="draft" data-id="${d.id}">
      <div class="card-head"><strong>To: ${esc(d.to_office)}</strong><span class="status ${d.status}">${esc(d.status)}</span></div>
      ${d.status === "pending" ? `
        <label>Recipient email <input name="to_address" value="${esc(d.to_address || "")}" placeholder="Copy it from the official page"></label>
        <label>Subject <input name="subject" value="${esc(d.subject)}"></label>
        <label>Message <textarea name="body">${esc(d.body)}</textarea></label>
        <div class="actions">
          <button class="primary approve">${smtp_enabled ? "Approve and send" : "Approve and open in my email app"}</button>
          <button class="save">Save edits</button>
          <button class="danger discard">Discard</button>
        </div>` : `<div class="meta">Subject: ${esc(d.subject)}</div><div class="meta" style="white-space:pre-wrap">${esc(d.body)}</div>
        ${d.status === "approved" ? `<div class="actions"><a href="${esc(d.mailto)}"><button>Open in my email app</button></a></div>` : ""}`}
    </article>`).join("");

  $$(".draft", list).forEach(el => {
    const id = el.dataset.id;
    const edits = () => ({ to_address: $("[name=to_address]", el).value, subject: $("[name=subject]", el).value, body: $("[name=body]", el).value });
    $(".save", el)?.addEventListener("click", async () => { await api(`/api/drafts/${id}`, { method: "PUT", body: edits() }); toast("Saved."); });
    $(".discard", el)?.addEventListener("click", async () => { await api(`/api/drafts/${id}/discard`, { method: "POST" }); loadDrafts(); refreshDraftCount(); });
    $(".approve", el)?.addEventListener("click", async () => {
      if (!confirm("Approve this email? You are confirming the content and recipient are correct.")) return;
      try {
        await api(`/api/drafts/${id}`, { method: "PUT", body: edits() });
        const d = await api(`/api/drafts/${id}/approve`, { method: "POST" });
        if (d.status === "sent") toast("Sent."); else window.location.href = d.mailto;
        loadDrafts(); refreshDraftCount();
      } catch (e) { toast(e.message); }
    });
  });
}

// ---------- scam check ----------
$("#scam-form").addEventListener("submit", async e => {
  e.preventDefault();
  const out = $("#scam-output");
  try {
    const r = await api("/api/scam-check", { method: "POST", body: { text: $("#scam-input").value } });
    const label = { low: "Low risk", medium: "Suspicious", high: "High risk: likely a scam" }[r.risk_level];
    out.innerHTML = `<div class="risk ${r.risk_level}">${label} (score ${r.score}/100)</div>
      ${r.signals.map(s => `<div class="signal">${esc(s.explanation)}<br><code>${esc(s.evidence)}</code></div>`).join("")}
      ${r.trusted_links.length ? `<p>Official links found: ${r.trusted_links.map(esc).join(", ")}</p>` : ""}
      <ul>${r.advice.map(a => `<li>${esc(a)}</li>`).join("")}</ul>`;
  } catch (err) { out.innerHTML = `<p class="error">${esc(err.message)}</p>`; }
});

// ---------- profile ----------
const PROFILE_FIELDS = [
  ["About you"],
  ["nationality", "Nationality", "text"],
  ["date_of_birth", "Date of birth", "date"],
  ["city", "City", "text"],
  ["Education"],
  ["current_education", "Current / last qualification", "text", "e.g. FSc Pre-Engineering, A-Levels, BS Computer Science"],
  ["field_of_study", "Field studied", "text"],
  ["intended_field", "Field you want to study", "text"],
  ["grade_percent", "HSSC / A-Level percentage", "number", "Bachelor's applicants", "0.1"],
  ["cgpa", "CGPA", "number", "Master's applicants", "0.01"],
  ["cgpa_scale", "CGPA scale", "number", "Usually 4", "0.1"],
  ["graduation_year", "Graduation year", "number"],
  ["Tests"],
  ["ielts", "IELTS overall", "number", "", "0.5"],
  ["toefl_ibt", "TOEFL iBT", "number"],
  ["pte", "PTE Academic", "number"],
  ["duolingo", "Duolingo English Test", "number"],
  ["sat", "SAT", "number"],
  ["gre", "GRE", "number"],
  ["jlpt", "JLPT level", "select", ["", "N1", "N2", "N3", "N4", "N5"]],
  ["Experience and preferences"],
  ["work_experience_months", "Work experience (months)", "number"],
  ["financial_need", "Need financial support to study abroad?", "select", ["", "yes", "no"]],
  ["preferred_countries", "Preferred countries (comma-separated)", "text"],
];

async function loadProfile() {
  const p = await api("/api/profile");
  const form = $("#profile-form");
  form.innerHTML = PROFILE_FIELDS.map(f => {
    if (f.length === 1) return `<h3>${f[0]}</h3>`;
    const [name, label, type, extra, step] = f;
    let value = p[name];
    if (name === "preferred_countries") value = (value || []).join(", ");
    if (name === "financial_need") value = value === true ? "yes" : value === false ? "no" : "";
    if (type === "select") {
      return `<label>${label}<select name="${name}">${extra.map(o => `<option value="${o}" ${String(value ?? "") === o ? "selected" : ""}>${o || "-"}</option>`).join("")}</select></label>`;
    }
    return `<label>${label}<input name="${name}" type="${type}" ${step ? `step="${step}"` : ""} value="${esc(value ?? "")}" placeholder="${esc(typeof extra === "string" ? extra : "")}"></label>`;
  }).join("") + `<div class="full"><button class="primary" type="submit">Save profile</button></div>`;
  $("#profile-notes").innerHTML = p.notes && p.notes.length
    ? `<h3>What the assistant remembers</h3><ul>${p.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
}

$("#profile-form").addEventListener("submit", async e => {
  e.preventDefault();
  const updates = {};
  for (const [k, v] of new FormData(e.target)) {
    if (k === "financial_need") updates[k] = v === "" ? null : v === "yes";
    else updates[k] = v === "" ? null : v;
  }
  try { await api("/api/profile", { method: "PUT", body: { updates } }); toast("Profile saved."); }
  catch (err) { toast(err.message); }
});

// ---------- reviewer ----------
async function loadReviews() {
  const rows = await api("/api/admin/reviews");
  const list = $("#review-list");
  if (!rows.length) { list.innerHTML = `<p class="muted">No pending changes. Run the monitor to check official pages.</p>`; return; }
  list.innerHTML = rows.map(r => `
    <article class="review" data-id="${r.id}">
      <div class="card-head"><strong>${esc(r.scholarship_id)}</strong><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Open page</a></div>
      <p>${esc(r.summary || "")}</p>
      <details><summary>Page diff</summary><pre>${esc(r.diff)}</pre></details>
      <label>Catalog changes to apply (JSON: dotted path to new value)
        <textarea name="patch" rows="4">${esc(JSON.stringify(r.proposed_patch || {}, null, 2))}</textarea></label>
      <label>Note <input name="note"></label>
      <div class="actions"><button class="primary approve">Approve and mark verified</button><button class="danger reject">Reject</button></div>
    </article>`).join("");
  $$(".review", list).forEach(el => {
    const id = el.dataset.id;
    const note = () => $("[name=note]", el).value || null;
    $(".approve", el).addEventListener("click", async () => {
      let patch;
      try { patch = JSON.parse($("[name=patch]", el).value || "{}"); } catch (_) { toast("Patch is not valid JSON."); return; }
      try { await api(`/api/admin/reviews/${id}/approve`, { method: "POST", body: { patch, note: note() } }); toast("Approved."); loadReviews(); }
      catch (e) { toast(e.message); }
    });
    $(".reject", el).addEventListener("click", async () => {
      await api(`/api/admin/reviews/${id}/reject`, { method: "POST", body: { note: note() } }); loadReviews();
    });
  });
}

$("#run-monitor").addEventListener("click", async () => {
  toast("Checking official pages... this can take a minute.");
  try { const r = await api("/api/admin/monitor/run", { method: "POST" }); toast(`Done: ${r.changed} changed, ${r.baseline} new, ${r.unchanged} unchanged, ${r.error} errors.`); loadReviews(); }
  catch (e) { toast(e.message); }
});

// ---------- boot ----------
(async function boot() {
  const token = load("sp_token");
  if (!token) return showAuth();
  state.token = token;
  try { state.user = await api("/api/me"); startApp(); }
  catch (_) { logout(); }
})();
