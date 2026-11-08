// LoomAI team dashboard. Talks only to this server's /api.
// Sign-in: `loom dashboard` opens this page with a one-time #login=<code>; we swap it for a dashboard token.

const TOKEN_KEY = "loom.dashboard.token";
const MAX_EVENTS = 300;
const INSTALL_SCRIPT_URL = "https://raw.githubusercontent.com/AskArtwentythree/loom-ai/main/install.sh";
const HOSTED_SERVER = "https://d1a5peogdvb7j1.cloudfront.net";
const ROLES = { owner: "Owner: manages people too", member: "Member: asks and adds", viewer: "Viewer: only asks" };

const AGENTS = {
  "cursor-vscode": "Cursor", cursor: "Cursor", "claude-code": "Claude Code", "claude-ai": "Claude app",
  "loom-cli": "Terminal", "loom-watch": "Watcher", "daddy-cli": "Terminal", "daddy-watch": "Watcher",
  "cursor-hook": "Cursor", "claude-hook": "Claude Code", dashboard: "Dashboard",
  "mcp-smoke": "Test client", "mcp-client": "MCP client",
};
const FILTERS = {
  all: () => true,
  stopped: (e) => e.decision !== "allow",
  deliveries: (e) => e.action.startsWith("delivery.") || e.action === "agent.share_blocked",
  knowledge: (e) => /^(knowledge|note|brief|files?|ingest)\./.test(e.action),
};

const S = {
  token: read(TOKEN_KEY), me: null, team: null, stats: null, events: [], inbox: [],
  waiting: [], invites: [], rules: null,
  socket: null, retry: 1000, fresh: null,
  feedFilter: "all", feedWho: null, feedTab: "feed", inboxTab: "new",
  projects: {}, asks: {},
};

// ── Small helpers ────────────────────────────────────────────────────────────
const $ = (s, root = document) => root.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const clip = (v, n) => { const t = String(v ?? ""); return t.length > n ? `${t.slice(0, n - 1)}…` : t; };
const agentName = (c) => AGENTS[c] ?? c ?? "";
const initials = (name) => String(name || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
const plural = (n, w) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;
const cap = (s) => String(s || "").replace(/^\w/, (c) => c.toUpperCase());
function read(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
function write(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* storage blocked */ } }
function debounce(fn, ms) { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; }
function ago(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
function until(iso) {
  const hours = Math.max(0, (new Date(iso).getTime() - Date.now()) / 3600000);
  if (hours < 1) return "in under an hour";
  if (hours < 36) return `in ${Math.round(hours)} h`;
  return `in ${Math.round(hours / 24)} days`;
}
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const bytes = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

const I = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ICON = {
  home: I('<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/>'),
  activity: I('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>'),
  team: I('<circle cx="9" cy="8" r="3.2"/><path d="M3 20c.7-3.4 3.1-5.2 6-5.2s5.3 1.8 6 5.2"/><path d="M16 4.6a3 3 0 0 1 0 6.2M18.5 14.6c1.3.8 2.2 2.6 2.5 5.4"/>'),
  inbox: I('<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5 5h14l2 8v6H3v-6z"/>'),
  rules: I('<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>'),
  ask: I('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
  file: I('<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/>'),
  skill: I('<path d="m12 3 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.9z"/>'),
  message: I('<path d="M4 5h16v11H9l-5 4z"/>'),
  door: I('<path d="M6 21V4h12v17"/><path d="M3 21h18"/><path d="M14 12h.01"/>'),
  link: I('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>'),
  mail: I('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>'),
  plus: I('<path d="M12 5v14M5 12h14"/>'),
};

// ── API ──────────────────────────────────────────────────────────────────────
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${S.token}`, "X-Loom-Client": "dashboard" },
    body: options.body && typeof options.body !== "string" ? JSON.stringify(options.body) : options.body,
  });
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) { signOut("This dashboard session has ended. Open a new one from your terminal."); throw new Error("unauthorized"); }
  if (!res.ok) throw new Error(body.reason || body.detail?.[0]?.msg || res.statusText);
  return body;
}

async function exchangeLoginCode(code) {
  const res = await fetch("/api/dashboard/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.reason || "This sign-in link no longer works.");
  return body.token;
}

// ── Toasts and modals ────────────────────────────────────────────────────────
function toast(text, err = false) {
  const el = document.createElement("div");
  el.className = `toast ${err ? "err" : ""}`;
  el.textContent = text;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 3600);
}
let lastFocus = null;
function openModal(html, { wide = false } = {}) {
  lastFocus = document.activeElement;
  const ov = $("#overlay");
  ov.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">${html}</div>`;
  ov.classList.add("open");
  const first = ov.querySelector("input:not([type=radio]), textarea, select, button:not(.x)");
  (first || ov.querySelector(".x"))?.focus();
  return ov.firstElementChild;
}
function closeModal() {
  const ov = $("#overlay");
  ov.classList.remove("open");
  ov.innerHTML = "";
  lastFocus?.focus?.();
}
const modalHead = (title) => `<div class="modal-h"><h2>${title}</h2><button class="x" data-act="close" aria-label="Close">×</button></div>`;

// ── Markdown (small, escape-first) ───────────────────────────────────────────
function inline(t) {
  return t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|\W)\*([^*\s][^*]*)\*(?=\W|$)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function md(src) {
  const out = [];
  const blocks = esc(src || "").split(/^```.*$/m);
  blocks.forEach((block, i) => {
    if (i % 2) { out.push(`<pre><code>${block.replace(/^\n|\n$/g, "")}</code></pre>`); return; }
    let list = null, para = [];
    const flush = () => {
      if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; }
      if (list) { out.push(`</${list}>`); list = null; }
    };
    for (const line of block.split("\n")) {
      const h = line.match(/^(#{1,3})\s+(.*)/);
      const li = line.match(/^\s*(?:[-*]|(\d+)\.)\s+(.*)/);
      const q = line.match(/^&gt;\s?(.*)/);
      if (h) { flush(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); }
      else if (li) {
        if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; }
        const tag = li[1] ? "ol" : "ul";
        if (list !== tag) { if (list) out.push(`</${list}>`); out.push(`<${tag}>`); list = tag; }
        out.push(`<li>${inline(li[2])}</li>`);
      } else if (q) { flush(); out.push(`<blockquote>${inline(q[1])}</blockquote>`); }
      else if (!line.trim()) flush();
      else { if (list) { out.push(`</${list}>`); list = null; } para.push(line.trim()); }
    }
    flush();
  });
  return out.join("");
}

// ── Gate ─────────────────────────────────────────────────────────────────────
function showGate(msg) {
  $("#app").hidden = true;
  $("#gate").hidden = false;
  if (msg) $("#gateMsg").textContent = msg;
}
function signOut(msg) {
  write(TOKEN_KEY, null);
  S.token = null;
  S.socket?.close();
  showGate(msg);
}

// ── Data ─────────────────────────────────────────────────────────────────────
const isAdmin = () => !!S.team?.is_admin;
const myProjects = () => (S.team?.projects || []).filter((p) => p.my_role);
const canManage = (p) => isAdmin() || p?.my_role === "owner";
const canInvite = () => isAdmin() || myProjects().some((p) => p.my_role === "owner");
const isFormer = (m) => m.devices.length > 0 && m.devices.every((d) => d.revoked_at);
const stoppedToday = () => S.stats?.stopped?.total ?? 0;
const unread = () => S.inbox.filter((i) => i.status === "new").length;

async function loadTeam() {
  const data = await api("/api/family");
  S.team = { ...data, team: data.family };
  renderChrome();
}
async function loadMe() { S.me = await api("/api/me"); }
async function loadStats() { if (isAdmin()) S.stats = await api("/api/stats"); }
async function loadAudit() { if (isAdmin()) S.events = (await api("/api/audit?limit=200")).reverse(); }
async function loadInbox() { S.inbox = await api("/api/inbox?all=true"); }
async function loadWaiting() { S.waiting = canInvite() ? await api("/api/join/requests") : []; }
async function loadInvites() { S.invites = canInvite() ? await api("/api/invites") : []; }
async function loadRules() { S.rules = await api("/api/guard/rules"); }
async function loadProject(slug, force = false) {
  const c = S.projects[slug] || (S.projects[slug] = {});
  const jobs = [];
  if (force || c.brief == null) jobs.push(api(`/api/projects/${encodeURIComponent(slug)}/brief`).then((r) => { c.brief = r.brief; }).catch((e) => { c.briefErr = e.message; }));
  if (force || !c.files) jobs.push(api(`/api/projects/${encodeURIComponent(slug)}/files`).then((r) => { c.files = r.files; }).catch((e) => { c.filesErr = e.message; }));
  await Promise.all(jobs);
  return c;
}

// ── Ledger sentences (same vocabulary as the backend's audit actions) ───────
function isNoise(e) {
  if (e.action === "agent.cursor.stop" || e.action === "dashboard.login") return true;
  return e.client === "dashboard" && /^(device\.(online|offline)|brief\.read|files\.list|file\.read)$/.test(e.action);
}
function describe(e) {
  const d = e.detail || {};
  const ok = e.decision === "allow";
  const q = (t) => `<q>${esc(clip(t, 110))}</q>`;
  const f = (n) => `<i>${esc(n)}</i>`;
  const who = (name, email) => `<b>${esc(name)}</b> ${esc(email)}`;
  switch (e.action) {
    case "knowledge.ask": return `${ok ? "asked" : "tried to ask"} ${q(d.question)}${ok && d.sources ? ` · ${plural(d.sources.length, "source")}` : ""}`;
    case "knowledge.search": return `${ok ? "searched for" : "tried to search for"} ${q(d.query)}`;
    case "delivery.send": return `${ok ? "sent" : "tried to send"} ${f(d.filename)} to <b>${esc(d.to)}</b>`;
    case "delivery.accept": return `accepted ${f(d.filename ?? "a delivery")}${d.sender ? ` from <b>${esc(d.sender)}</b>` : ""}`;
    case "delivery.decline": return "declined a delivery";
    case "agent.share_blocked": return `was stopped on the laptop before sending ${f(d.filename)} to <b>${esc(d.to)}</b>`;
    case "agent.prompt_blocked": return "was stopped from putting a secret into a prompt";
    case "agent.shell":
    case "agent.mcp": {
      const what = e.action === "agent.shell" ? `<i>${esc(clip(d.target, 90))}</i>` : `the tool ${f(d.target)}`;
      if (d.verdict === "ask") return `had to ask before running ${what}`;
      return ok ? `ran ${what}` : `was stopped from running ${what}`;
    }
    case "agent.guard_blocked": {
      const verb = { shell: "running", read: "reading", mcp: "calling" }[d.kind] ?? "doing";
      return `was stopped on the laptop from ${verb} ${f(clip(d.target, 90))}`;
    }
    case "guard.rule_add": return `${ok ? "added" : "tried to add"} the guard rule ${f(d.pattern)} (${esc(d.verdict)} ${esc(d.kind)})`;
    case "guard.rule_remove": return "removed a guard rule";
    case "agent.cursor.after_mcp_execution": return `used the tool ${f(d.tool_name)}${d.mcp_server_name ? ` from ${esc(d.mcp_server_name)}` : ""}`;
    case "note.save": return `${ok ? "saved the note" : "tried to save the note"} ${q(d.title)}`;
    case "brief.read": return "read the project brief";
    case "files.list": return "listed the project's files";
    case "file.read": return `opened ${f(d.path)}`;
    case "ingest.upload": return `${ok ? "added" : "tried to add"} ${plural(d.indexed ?? d.files ?? 0, "file")} to project knowledge`;
    case "ingest.prune": return `removed ${plural(d.removed ?? 0, "deleted file")} from project knowledge`;
    case "ingest.github": case "ingest.github.request": return `imported ${f(d.repo)} from GitHub`;
    case "invite.create": return d.email ? `invited ${esc(d.email)} as ${esc(d.role)}` : `made a team link (${esc(d.role)})`;
    case "invite.cancel": return "cancelled an invite";
    case "family.join": return d.approved_by ? `${esc(d.email)} joined the team, let in by <b>${esc(d.approved_by)}</b>` : `${esc(d.email)} joined the team`;
    case "family.join_request": return `${who(d.name, d.email)} asked to join with a team link`;
    case "project.join": return !ok ? "tried to join a project with a link" : d.already ? "used a link to a project they were already in" : "joined the project with its link";
    case "join.approve": return `let ${who(d.name, d.email)} into the team`;
    case "join.deny": return `turned away ${who(d.name, d.email)}`;
    case "family.create": return "started the team";
    case "project.create": return `created the project ${f(d.slug)}`;
    case "member.add": return `added ${esc(d.email)} as ${esc(d.role)}`;
    case "member.remove": return `removed ${esc(d.email)} from the project`;
    case "device.online": return "came online";
    case "device.offline": return "went offline";
    case "device.revoke": return "revoked a computer";
    case "dashboard.login": return "opened the dashboard";
    case "user.rename": return ok ? `changed their name from <b>${esc(d.old)}</b> to <b>${esc(d.new)}</b>` : `tried to change their name to <b>${esc(d.new)}</b>`;
    default: return f(e.action);
  }
}
function evRow(e) {
  const who = e.user ?? "LoomAI";
  const agent = agentName(e.client) || e.device || "";
  const bad = e.decision !== "allow";
  const meta = [agent, e.project].filter(Boolean).map(esc).join(" · ");
  return `<div class="ev ${esc(e.decision)} ${e.id === S.fresh ? "fresh" : ""}" data-ev="${esc(e.id)}">
    <div class="tx"><b>${esc(who)}</b> ${describe(e)}</div>
    <time datetime="${esc(e.at)}" title="${esc(new Date(e.at).toLocaleString())}">${ago(e.at)}</time>
    ${meta ? `<div class="mt">${meta}</div>` : ""}
    ${bad ? `<div class="why"><b>Stopped</b>${e.reason ? `: ${esc(e.reason)}` : ""}</div>` : ""}
  </div>`;
}
const meaningful = () => S.events.filter((e) => !isNoise(e));

// ── Chrome ───────────────────────────────────────────────────────────────────
function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  return { view: parts[0] || "home", a: parts[1], b: parts[2] };
}
function renderChrome() {
  if (!S.team) return;
  const r = route();
  $("#teamChip").innerHTML = `<i>${esc(initials(S.team.team))}</i>${esc(S.team.team)}`;
  $("#me").textContent = initials(S.team.me);
  $("#me").title = `Signed in as ${S.team.me}`;
  $("#inviteBtn").hidden = !canInvite();
  const btn = (href, label, icon, on, n = "", cls = "") =>
    `<a class="nav-btn ${on ? "on" : ""}" href="${href}" ${on ? 'aria-current="page"' : ""}>${icon}<span>${label}</span>${n !== "" ? `<span class="n ${cls}">${n}</span>` : ""}</a>`;
  const stopped = stoppedToday();
  const projects = S.team.projects.map((p) => btn(`#/p/${encodeURIComponent(p.slug)}`, esc(p.name), `<span class="glyph">${esc(initials(p.name))}</span>`, r.view === "p" && r.a === p.slug, p.documents ? p.documents.toLocaleString() : "")).join("");
  $("#sidebar").innerHTML = `
    <div>
      ${btn("#/home", "Home", ICON.home, r.view === "home")}
      ${btn("#/activity", "Activity", ICON.activity, r.view === "activity", isAdmin() && stopped ? stopped : "", "red")}
      ${btn("#/team", "Team", ICON.team, r.view === "team", S.waiting.length || "", "amber")}
      ${btn("#/inbox", "Inbox", ICON.inbox, r.view === "inbox", unread() || "", "blue")}
    </div>
    <div class="proj-list">
      <div class="side-h">Projects</div>
      ${projects || '<p class="side-none">None yet</p>'}
      <button class="nav-btn add" data-act="newproject">${ICON.plus}<span>New project</span></button>
    </div>
    <div>
      <div class="side-h">Security</div>
      ${btn("#/rules", "House rules", ICON.rules, r.view === "rules")}
    </div>`;
}
function setLive(state, label) {
  const el = $("#live");
  el.dataset.state = state;
  el.lastElementChild.textContent = label;
}

// ── Views ────────────────────────────────────────────────────────────────────
function render() {
  if (!S.team) return;
  renderChrome();
  const r = route();
  const main = $("#main");
  const views = { home: viewHome, activity: viewActivity, team: viewTeam, inbox: viewInbox, rules: viewRules, p: viewProject };
  main.innerHTML = `<div class="page">${(views[r.view] || viewHome)(r)}</div>`;
  if (r.view === "p") hydrateProject(r);
}

function sparkline(series, cls = "") {
  const max = Math.max(1, ...series);
  const step = 120 / (series.length - 1);
  const pts = series.map((v, i) => `${(i * step).toFixed(1)},${(25 - (v / max) * 23).toFixed(1)}`).join(" ");
  const quiet = series.every((v) => v === 0);
  return `<svg class="spark ${quiet ? "quiet" : ""} ${cls}" viewBox="0 0 120 26" preserveAspectRatio="none" aria-hidden="true"><line x1="0" y1="25.5" x2="120" y2="25.5"/><polyline points="${pts}"/></svg>`;
}
const head = (title, subs = [], actions = "") => `<div class="page-h"><div><h1>${title}</h1>${subs.filter(Boolean).map((s) => `<p class="sub">${s}</p>`).join("")}</div>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;

function statsStrip() {
  if (!isAdmin()) return "";
  const st = S.stats;
  if (!st) return `<div class="stats"><div class="stat"><div class="k">Loading</div><div class="v">–</div></div></div>`;
  const cell = (k, v, series, cls = "") => `<div class="stat ${cls}"><div class="k">${k}</div><div class="v">${v.toLocaleString()}</div>${series ? sparkline(series) : ""}</div>`;
  return `<div class="stats">
    ${cell("Questions answered", st.questions.total, st.questions.series)}
    ${cell("Deliveries", st.delivered.total, st.delivered.series)}
    ${cell("Stopped", st.stopped.total, st.stopped.series, `stop ${st.stopped.total ? "" : "zero"}`)}
    ${cell("Agents active", st.agents)}
  </div>`;
}
function askBar(id, { project = null, placeholder } = {}) {
  const opts = myProjects();
  const select = project ? "" : `<select aria-label="Project" data-ask-project><option value="">All my projects</option>${opts.map((p) => `<option value="${esc(p.slug)}">${esc(p.name)}</option>`).join("")}</select>`;
  return `<form class="ask" data-ask="${esc(id)}" ${project ? `data-project="${esc(project)}"` : ""}>
    ${ICON.ask}<input name="q" autocomplete="off" placeholder="${esc(placeholder || "How do we deploy?")}" aria-label="Ask a question about your projects">
    ${select}<button class="btn primary sm" type="submit">Ask</button></form>
    <div data-answers="${esc(id)}">${(S.asks[id] || []).map(answerCard).join("")}</div>`;
}

// People who used a team link and wait for a yes
function waitingCard() {
  if (!S.waiting.length) return "";
  return `<div class="card door">
    <div class="card-h">${ICON.door}Waiting at the door<span class="right">used a team link · nobody gets in until you say so</span></div>
    ${S.waiting.map((r) => `<div class="item">
      <span class="avatar">${esc(initials(r.name))}</span>
      <div style="min-width:0">
        <div class="t">${esc(r.name)} <span class="mail">${esc(r.email)}</span></div>
        <div class="s">wants to join ${r.project ? `<b>${esc(r.project)}</b>` : "the team"} · from ${esc(r.device)} · asked ${ago(r.created_at)} · link by ${esc(r.link_by)}</div>
      </div>
      <div class="row"><button class="btn danger sm" data-act="decide" data-verdict="deny" data-id="${esc(r.id)}" data-name="${esc(r.name)}">Turn away</button><button class="btn primary sm" data-act="decide" data-verdict="approve" data-id="${esc(r.id)}" data-name="${esc(r.name)}">Let in</button></div>
    </div>`).join("")}
  </div>`;
}

function viewHome() {
  const fam = S.team;
  const agents = new Set(fam.members.flatMap((m) => m.devices.flatMap((d) => d.agents || [])));
  const recent = meaningful().slice(0, 8);
  return `
    ${head(esc(fam.team), [`${plural(fam.members.filter((m) => !isFormer(m)).length, "member")} · ${plural(fam.projects.length, "project")} · ${plural(agents.size, "agent")} seen this week`])}
    ${waitingCard()}
    ${S.waiting.length ? '<div style="height:16px"></div>' : ""}
    ${askBar("home")}
    ${isAdmin() ? `<h2 class="sec">Last 24 hours</h2>${statsStrip()}` : ""}
    <h2 class="sec">Recent activity${isAdmin() ? '<span class="right"><a href="#/activity">View all</a></span>' : ""}</h2>
    <div class="card">
      ${isAdmin() ? (recent.length ? `<div class="feed">${recent.map(evRow).join("")}</div>` : '<p class="empty">No activity yet. It shows up here once an agent asks or shares something.</p>') : '<p class="empty">Only team admins can see activity.</p>'}
    </div>`;
}

function viewActivity(r) {
  if (!isAdmin()) return head("Activity", ["Only team admins can see activity."]);
  if (r.a && FILTERS[r.a]) S.feedFilter = r.a;
  const base = meaningful();
  const people = [...new Set(base.map((e) => e.user).filter(Boolean))];
  const list = base.filter(FILTERS[S.feedFilter]).filter((e) => !S.feedWho || e.user === S.feedWho);
  const count = (k) => base.filter(FILTERS[k]).length;
  const pill = (k, label) => `<button class="pill ${S.feedFilter === k ? "on" : ""}" data-act="filter" data-k="${k}" aria-pressed="${S.feedFilter === k}">${label}${k !== "all" && count(k) ? ` <span class="n ${k === "stopped" ? "red" : ""}">${count(k)}</span>` : ""}</button>`;
  const body = S.feedTab === "feed"
    ? (list.length ? `<div class="feed">${list.map(evRow).join("")}</div>` : `<p class="empty">${S.feedFilter === "stopped" ? "Nothing has been stopped." : "Nothing matches these filters."}</p>`)
    : `<table class="tbl click">
        <colgroup><col style="width:92px"><col style="width:160px"><col><col class="hide-sm" style="width:130px"><col style="width:88px"><col style="width:64px"></colgroup>
        <thead><tr><th>Time</th><th>Who</th><th>Action</th><th class="hide-sm">Project</th><th>Decision</th><th class="num">ms</th></tr></thead>
        <tbody>${list.map((e) => `<tr class="${esc(e.decision)} ${e.id === S.fresh ? "fresh" : ""}" data-ev="${esc(e.id)}" tabindex="0">
          <td class="t">${clock(e.at)}</td>
          <td class="w"><b>${esc(e.user ?? "LoomAI")}</b><small>${esc(agentName(e.client) || e.device || "")}</small></td>
          <td><code>${esc(e.action)}</code>${e.reason && e.decision !== "allow" ? `<span class="why">${esc(e.reason)}</span>` : ""}</td>
          <td class="m hide-sm">${esc(e.project ?? "–")}</td>
          <td><span class="outcome ${esc(e.decision)}">${esc(e.decision)}</span></td>
          <td class="m num">${e.latency_ms ?? "–"}</td></tr>`).join("") || '<tr><td colspan="6" class="empty">Nothing matches these filters.</td></tr>'}</tbody></table>`;
  return `
    ${head("Activity", ["Every request from every agent in the team, as the gateway decided it."], '<span class="live-note">Updates live</span>')}
    <nav class="tabs"><a href="#/activity" data-act="tab" data-t="feed" class="${S.feedTab === "feed" ? "on" : ""}">Feed</a><a href="#/activity" data-act="tab" data-t="log" class="${S.feedTab === "log" ? "on" : ""}">Technical log</a></nav>
    <div class="pills">
      ${pill("all", "All")}${pill("stopped", "Stopped")}${pill("deliveries", "Deliveries")}${pill("knowledge", "Knowledge")}
      ${people.length ? '<span class="pill-sep"></span>' : ""}
      ${people.map((p) => `<button class="pill ${S.feedWho === p ? "on" : ""}" data-act="who" data-who="${esc(p)}" aria-pressed="${S.feedWho === p}">${esc(p)}</button>`).join("")}
    </div>
    <div class="card">${body}</div>`;
}

function invitesCard() {
  if (!canInvite()) return "";
  const rows = S.invites.map((i) => {
    const where = i.project ? `the <b>${esc(i.project)}</b> project` : "the team";
    const title = i.team_link ? `Team link to ${where}` : `${esc(i.name)} <span class="mail">${esc(i.email)}</span> to ${where}`;
    const details = [
      cap(i.role),
      i.team_link ? `${i.uses} of ${i.max_uses} used` : "not used yet",
      i.team_link && i.waiting ? `<span class="amber">${i.waiting} waiting for you</span>` : null,
      `ends ${until(i.expires_at)}`,
      `by ${esc(i.created_by)}`,
    ].filter(Boolean);
    return `<div class="item">
      <span class="kind">${i.team_link ? ICON.link : ICON.mail}</span>
      <div style="min-width:0"><div class="t">${title}</div><div class="s">${details.join(" · ")}</div></div>
      <div class="row"><button class="btn ghost sm" data-act="cancelinvite" data-id="${esc(i.id)}">Cancel</button></div>
    </div>`;
  }).join("");
  return `<h2 class="sec">Open invites<span class="right"><a href="#/team" data-act="invite">+ Invite people</a></span></h2>
    <div class="card">${rows || '<p class="empty">No open invites. Make one with <b>+ Invite</b>; team members join a project\'s link at once, newcomers wait for your yes.</p>'}</div>`;
}

function viewTeam() {
  const fam = S.team;
  const computers = (m) => m.devices.filter((d) => d.name !== "Dashboard");
  const active = fam.members.filter((m) => !isFormer(m));
  const gone = fam.members.filter(isFormer);
  const nComputers = active.reduce((n, m) => n + computers(m).filter((d) => !d.revoked_at).length, 0);
  const rows = active.map((m) => {
    const devs = computers(m);
    const person = `<td rowspan="${Math.max(1, devs.length)}"><div class="person"><span class="avatar">${esc(initials(m.name))}</span><div style="min-width:0"><b>${esc(m.name)}${m.is_admin ? ' <span class="role admin">admin</span>' : ""}</b><small>${esc(m.email)}</small></div></div></td>`;
    if (!devs.length) return `<tr class="first">${person}<td colspan="4" class="m">No computer connected yet</td></tr>`;
    return devs.map((d, i) => {
      const agents = [...new Set((d.agents || []).map(agentName))].join(", ") || "None this week";
      const status = d.revoked_at ? '<span class="status"><i class="dot off"></i>Revoked</span>'
        : d.online ? '<span class="status on"><i class="dot on"></i>Online</span>'
        : `<span class="status"><i class="dot"></i>${esc(cap(ago(d.last_seen_at)))}</span>`;
      const canRevoke = !d.revoked_at && (isAdmin() || m.name === fam.me);
      return `<tr class="${i === 0 ? "first" : ""} ${d.revoked_at ? "revoked" : ""}">${i === 0 ? person : ""}
        <td><code>${esc(d.name)}</code></td><td>${esc(agents)}</td><td>${status}</td>
        <td class="num">${canRevoke ? `<button class="btn ghost sm" data-act="revoke" data-id="${esc(d.id)}" data-name="${esc(d.name)}">Revoke</button>` : ""}</td></tr>`;
    }).join("");
  }).join("");
  return `
    ${head("Team", [`${plural(active.length, "member")} · ${plural(nComputers, "computer")} connected`], canInvite() ? '<button class="btn primary" data-act="invite">+ Invite people</button>' : "")}
    ${waitingCard()}
    ${S.waiting.length ? '<div style="height:16px"></div>' : ""}
    <div class="card"><table class="tbl team">
      <colgroup><col style="width:30%"><col style="width:20%"><col><col style="width:130px"><col style="width:90px"></colgroup>
      <thead><tr><th>Person</th><th>Computer</th><th>Agents seen this week</th><th>Status</th><th><span class="sr">Actions</span></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${gone.length ? `<p class="foot">Revoked: ${gone.map((m) => esc(m.name)).join(", ")}</p>` : ""}
    ${invitesCard()}`;
}

function viewInbox() {
  const items = S.inbox.filter((i) => (S.inboxTab === "new" ? i.status === "new" : true));
  const kindIcon = (k) => ICON[k] || ICON.file;
  return `
    ${head("Inbox", ["Files, skills and messages teammates sent you. With <code>loom watch</code> open, they're saved and installed on your computer as they arrive."])}
    <nav class="tabs"><a href="#/inbox" data-act="itab" data-t="new" class="${S.inboxTab === "new" ? "on" : ""}">Waiting<span class="n">${unread() || ""}</span></a><a href="#/inbox" data-act="itab" data-t="all" class="${S.inboxTab === "all" ? "on" : ""}">All</a></nav>
    <div class="card">${items.map((i) => {
      const install = `loom accept ${i.id}`;
      return `<div class="item">
      <span class="kind" aria-label="${esc(i.kind)}">${kindIcon(i.kind)}</span>
      <div style="min-width:0">
        <div class="t">${i.kind === "message" ? `Message from ${esc(i.from)}` : `<code>${esc(i.filename)}</code>`}</div>
        <div class="s">${i.kind === "message" ? "" : `${esc(cap(i.kind))} from ${esc(i.from)} · `}${esc(i.project)} · ${ago(i.created_at)}${i.size_bytes && i.kind !== "message" ? ` · ${bytes(i.size_bytes)}` : ""}</div>
        ${i.note ? `<div class="note">${esc(i.note)}</div>` : ""}
        ${i.status === "new" && i.kind !== "message" ? `<div class="note hint-line">To ${i.kind === "skill" ? "install it into your agents" : "save it"}, run <code>${esc(install)}</code> on your computer.</div>` : ""}
      </div>
      <div class="row">${i.status === "new"
        ? `<button class="btn ghost sm" data-act="decline" data-id="${esc(i.id)}">Decline</button>${i.kind === "message"
          ? `<button class="btn primary sm" data-act="accept" data-id="${esc(i.id)}">Open</button>`
          : `<button class="btn sm" data-act="copy" data-copy="${esc(install)}">Copy command</button>`}`
        : `<span class="status">${i.status === "accepted" ? "Accepted" : "Declined"}</span>`}</div>
    </div>`;
    }).join("") || `<p class="empty">${S.inboxTab === "new" ? "Nothing waiting. When a teammate's agent sends you something, it shows up here." : "Nobody has sent you anything yet."}</p>`}</div>`;
}

function viewRules() {
  const p = S.me?.providers || S.team.providers || {};
  const rules = S.rules;
  const own = rules?.family || [];
  const verdict = (v) => `<span class="outcome ${v === "allow" ? "allow" : v === "deny" ? "deny" : "ask"}">${esc(v)}</span>`;
  return `
    ${head("House rules", ["The gateway enforces these on every call, for every agent in the team."])}
    <div class="card"><dl class="rules">
      <dt>Identity</dt><dd>One revocable token per computer. Every call records which person, computer and agent made it.</dd>
      <dt>Access</dt><dd>Owner, member or viewer per project. Other projects' passages are never even searched. Viewers can ask but not add.</dd>
      <dt>Agent guard</dt><dd>Every shell command, file read and tool call an agent makes is checked first${rules ? `: ${rules.builtin.length} built-in rules${own.length ? `, plus ${own.length} of the team's own` : ""}` : ""}. Destructive commands are blocked, risky ones ask the person.</dd>
      <dt>Secrets</dt><dd>Checked on the laptop before anything is uploaded or sent, again on the server, and in prompts. <code>.env</code> files, keys and certificates can never be read by an agent or sent; no rule can turn that off.</dd>
      <dt>Deliveries</dt><dd>On the hosted LoomAI, every message, file and skill a teammate sends also passes a Bedrock Guardrail for credentials, ID numbers and prompt attacks.</dd>
      <dt>Joining</dt><dd>Newcomers with a team link wait until an admin or project owner lets them in. Agents can't approve anyone.</dd>
      <dt>Answers</dt><dd>Only from project knowledge, with sources. Below the relevance bar the answer is “Not in project knowledge.”</dd>
      <dt>Audit</dt><dd>Every allow, deny and error is logged with its latency and streamed here live.</dd>
      <dt>Models</dt><dd>Embeddings <code>${esc(p.embedder || "–")}</code>, reranker <code>${esc(p.reranker || "–")}</code>, answers <code>${esc(p.llm || "–")}</code></dd>
    </dl></div>
    ${own.length ? `<h2 class="sec">The team's own guard rules<span class="right">checked before the built-in ones · change them with <code>loom guard</code></span></h2>
      <div class="card"><table class="tbl"><colgroup><col style="width:80px"><col style="width:34%"><col style="width:90px"><col></colgroup>
      <thead><tr><th>Kind</th><th>Pattern</th><th>Decision</th><th>Why</th></tr></thead>
      <tbody>${own.map((x) => `<tr><td class="m">${esc(x.kind)}</td><td><code>${esc(x.pattern)}</code></td><td>${verdict(x.verdict)}</td><td>${esc(x.reason)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    ${S.me ? `<h2 class="sec">This session</h2><div class="card"><dl class="rules">
      <dt>Signed in as</dt><dd>${esc(S.me.user)}, ${esc(S.me.email)}</dd>
      <dt>Can do here</dt><dd>Ask, read projects, save notes, invite people and let them in, create projects and manage their members, as your role allows. Sending files and installing skills happen on your computer, through your agent or <code>loom</code>.</dd>
    </dl></div>` : ""}`;
}

function viewProject(r) {
  const p = S.team.projects.find((x) => x.slug === r.a);
  if (!p) return head("Project not found", [`You don't have access to <code>${esc(r.a)}</code>, or it doesn't exist.`]);
  const tab = ["files", "people", "ask"].includes(r.b) ? r.b : "brief";
  const canWrite = ["owner", "member"].includes(p.my_role);
  const members = S.team.members.filter((m) => m.projects.some((x) => x.slug === p.slug));
  const roleOf = (m) => m.projects.find((x) => x.slug === p.slug).role;
  const href = (t) => `#/p/${encodeURIComponent(p.slug)}${t === "brief" ? "" : `/${t}`}`;
  const role = p.my_role ? cap(p.my_role) : "Admin view";
  const actions = [
    canWrite ? `<button class="btn" data-act="note" data-slug="${esc(p.slug)}">Save a note</button>` : "",
    canManage(p) ? `<button class="btn primary" data-act="invite" data-slug="${esc(p.slug)}">+ Invite</button>` : "",
  ].join("");
  return `
    ${head(esc(p.name), [p.description ? esc(p.description) : "", `${role} · ${plural(p.members, "member")} · ${plural(p.documents, "file")} · ${plural(p.chunks, "passage")} indexed`], actions)}
    <nav class="tabs">
      <a href="${href("brief")}" class="${tab === "brief" ? "on" : ""}">Summary</a>
      <a href="${href("files")}" class="${tab === "files" ? "on" : ""}">Files<span class="n">${p.documents || ""}</span></a>
      <a href="${href("people")}" class="${tab === "people" ? "on" : ""}">People<span class="n">${members.length || ""}</span></a>
      <a href="${href("ask")}" class="${tab === "ask" ? "on" : ""}">Ask</a>
    </nav>
    ${tab === "brief" ? `<div class="grid brief-grid">
        <div class="card"><div class="card-h">Project brief<span class="right">Written by LoomAI from the project's files</span></div><div class="card-b" data-slot="brief"><p class="empty">Loading…</p></div></div>
        <div class="card"><div class="card-h">Members<span class="right"><a href="${href("people")}">${canManage(p) ? "Manage" : "All"}</a></span></div><table class="tbl"><tbody>${members.map((m) =>
          `<tr><td><div class="person"><span class="avatar">${esc(initials(m.name))}</span><div style="min-width:0"><b>${esc(m.name)}</b><small>${esc(cap(roleOf(m)))}</small></div></div></td></tr>`).join("")}</tbody></table></div></div>` : ""}
    ${tab === "files" ? `<input class="filter-in" data-act="filefilter" placeholder="Filter by path" aria-label="Filter files by path"><div class="card files" data-slot="files"><p class="empty">Loading…</p></div>` : ""}
    ${tab === "people" ? peopleTab(p, members, roleOf) : ""}
    ${tab === "ask" ? askBar(`p:${p.slug}`, { project: p.slug, placeholder: `What does ${p.name} use for auth?` }) : ""}`;
}

function peopleTab(p, members, roleOf) {
  const manage = canManage(p);
  const others = S.team.members.filter((m) => !isFormer(m) && !members.includes(m));
  const roleSelect = (m) => `<select class="sm" data-act="setrole" data-slug="${esc(p.slug)}" data-email="${esc(m.email)}" aria-label="${esc(m.name)}'s role">${Object.keys(ROLES).map((r) => `<option value="${r}" ${r === roleOf(m) ? "selected" : ""}>${cap(r)}</option>`).join("")}</select>`;
  const rows = members.map((m) => `<tr>
    <td><div class="person"><span class="avatar">${esc(initials(m.name))}</span><div style="min-width:0"><b>${esc(m.name)}</b><small>${esc(m.email)}</small></div></div></td>
    <td>${manage ? roleSelect(m) : `<span class="role ${esc(roleOf(m))}">${esc(roleOf(m))}</span>`}</td>
    <td class="num">${manage ? `<button class="btn ghost sm" data-act="removemember" data-slug="${esc(p.slug)}" data-email="${esc(m.email)}" data-name="${esc(m.name)}">Remove</button>` : ""}</td>
  </tr>`).join("");
  const add = manage ? `<div class="card-f">
      ${others.length ? `<form class="row" data-form="addmember" data-slug="${esc(p.slug)}">
        <select name="email" aria-label="Someone already in the team">${others.map((m) => `<option value="${esc(m.email)}">${esc(m.name)} · ${esc(m.email)}</option>`).join("")}</select>
        <select name="role" aria-label="Their role">${Object.keys(ROLES).map((r) => `<option value="${r}" ${r === "member" ? "selected" : ""}>${cap(r)}</option>`).join("")}</select>
        <button class="btn sm" type="submit">Add to project</button></form>` : '<span class="hint-line">Everyone in the team is already here.</span>'}
      <span class="sp"></span><button class="btn ghost sm" data-act="invite" data-slug="${esc(p.slug)}">Invite someone new</button>
    </div>` : "";
  return `<div class="card"><table class="tbl team-people"><colgroup><col><col style="width:150px"><col style="width:100px"></colgroup>
    <thead><tr><th>Person</th><th>Role</th><th><span class="sr">Actions</span></th></tr></thead>
    <tbody>${rows}</tbody></table>${add}</div>
    ${manage ? '<p class="foot">Removing someone cuts their agents off from this project straight away.</p>' : ""}`;
}

async function hydrateProject(r) {
  const slug = r.a;
  if (!S.team.projects.some((x) => x.slug === slug)) return;
  const c = await loadProject(slug);
  if (route().a !== slug) return;
  const briefSlot = $('[data-slot="brief"]');
  if (briefSlot) briefSlot.innerHTML = c.briefErr ? `<p class="err">${esc(c.briefErr)}</p>` : c.brief ? `<div class="md">${md(c.brief)}</div>` : '<p class="empty">No brief yet. Add files with <code>loom ingest .</code></p>';
  renderFiles(slug);
}
function renderFiles(slug, filter = "") {
  const slot = $('[data-slot="files"]');
  const c = S.projects[slug];
  if (!slot || !c) return;
  if (c.filesErr) { slot.innerHTML = `<p class="err" style="padding:16px">${esc(c.filesErr)}</p>`; return; }
  const list = (c.files || []).filter((f) => f.path.toLowerCase().includes(filter.toLowerCase()));
  slot.innerHTML = list.map((f) => {
    const ext = (f.path.match(/\.([a-z0-9]+)$/i)?.[1] || "txt").slice(0, 4);
    return `<button class="f" data-act="file" data-slug="${esc(slug)}" data-path="${esc(f.path)}"><span class="ext">${esc(ext)}</span><span class="p">${esc(f.path)}</span><span class="k">${f.kind === "note" ? "note" : ""}</span><span class="z">${bytes(f.size_bytes)} · ${ago(f.updated_at)}</span></button>`;
  }).join("") || `<p class="empty">${filter ? "No files match." : "No files yet. Add them with <code>loom ingest .</code>"}</p>`;
}

// ── Asking ───────────────────────────────────────────────────────────────────
function answerCard(a) {
  if (a.pending) return `<div class="card answer"><div class="card-h">${esc(clip(a.q, 90))}</div><div class="card-b"><span style="color:var(--faint)">Searching…</span></div></div>`;
  if (a.error) return `<div class="card answer refused"><div class="card-h">${esc(clip(a.q, 90))}</div><div class="card-b"><p class="err" style="margin:0">${esc(a.error)}</p></div></div>`;
  const res = a.res;
  const text = md(res.answer).replace(/\[(\d+)\]/g, (m, n) => (res.citations.some((c) => c.n === +n) ? `<span class="cite">${n}</span>` : m));
  return `<div class="card answer ${res.grounded ? "" : "refused"}">
    <div class="card-h"><span class="q">${esc(clip(a.q, 120))}</span><span class="right">${res.grounded ? plural(res.citations.length, "source") : "No matching sources"}</span></div>
    <div class="card-b"><div class="md">${text}</div></div>
    ${res.citations.length ? `<div class="srcs">${res.citations.map((c) => `<button class="src" data-act="file" data-slug="${esc(c.project)}" data-path="${esc(c.path)}"><span class="cite">${c.n}</span><span style="min-width:0"><span class="p">${esc(c.path)}:${esc(c.lines)}</span>${c.section ? `<span class="h">${esc(c.section)}</span>` : ""}</span><span class="s">${esc(c.project)}</span></button>`).join("")}</div>` : ""}
    ${res.top_score == null ? "" : `<div class="meta-line">Relevance ${res.top_score.toFixed(2)} (needs ${res.threshold})</div>`}
  </div>`;
}
async function ask(form) {
  const id = form.dataset.ask;
  const q = form.q.value.trim();
  if (!q) return;
  const project = form.dataset.project || form.querySelector("[data-ask-project]")?.value || null;
  const list = S.asks[id] || (S.asks[id] = []);
  const entry = { q, pending: true };
  list.unshift(entry);
  list.length = Math.min(list.length, 5);
  form.q.value = "";
  const paint = () => { const slot = document.querySelector(`[data-answers="${CSS.escape(id)}"]`); if (slot) slot.innerHTML = list.map(answerCard).join(""); };
  paint();
  try {
    entry.res = await api("/api/ask", { method: "POST", body: { query: q, project, session_key: `dashboard:${id}` } });
  } catch (e) { entry.error = e.message; }
  entry.pending = false;
  paint();
}

// ── Modals ───────────────────────────────────────────────────────────────────
async function openFile(slug, path) {
  openModal(`${modalHead(`<code style="font:500 15px var(--mono)">${esc(path)}</code>`)}<div data-slot="file"><p class="empty">Loading…</p></div>`, { wide: true });
  try {
    const f = await api(`/api/projects/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`);
    const slot = $('[data-slot="file"]');
    if (slot) slot.innerHTML = /\.mdx?$/i.test(path) ? `<div class="modal-b md">${md(f.content)}</div>` : `<pre class="raw">${esc(f.content)}</pre>`;
  } catch (e) { const slot = $('[data-slot="file"]'); if (slot) slot.innerHTML = `<div class="modal-b"><p class="err">${esc(e.message)}</p></div>`; }
}
function openEvent(id) {
  const e = S.events.find((x) => String(x.id) === String(id));
  if (!e) return;
  openModal(`${modalHead("Request details")}<div class="modal-b"><dl class="kv">
    <dt>When</dt><dd>${esc(new Date(e.at).toLocaleString())}</dd>
    <dt>Who</dt><dd>${esc(e.user ?? "LoomAI")}${e.device ? ` · ${esc(e.device)}` : ""}</dd>
    <dt>Agent</dt><dd>${esc(agentName(e.client) || "–")} <code>${esc(e.client ?? "")}</code></dd>
    <dt>Action</dt><dd><code>${esc(e.action)}</code></dd>
    <dt>Project</dt><dd>${esc(e.project ?? "–")}</dd>
    <dt>Decision</dt><dd><span class="outcome ${esc(e.decision)}">${esc(e.decision)}</span></dd>
    ${e.reason ? `<dt>Reason</dt><dd>${esc(e.reason)}</dd>` : ""}
    <dt>Latency</dt><dd>${e.latency_ms == null ? "–" : `${e.latency_ms} ms`}</dd>
    <dt>Detail</dt><dd><pre class="raw" style="padding:0;max-height:220px">${esc(JSON.stringify(e.detail || {}, null, 2))}</pre></dd>
  </dl></div>`);
}
function openNote(slug) {
  const m = openModal(`${modalHead("Save a note")}<form data-form="note"><div class="modal-b">
    <p>Every teammate's agent can find it right away. Notes with secrets in them are refused.</p>
    <p class="err" hidden></p>
    <div class="field"><label for="nt">Title</label><input id="nt" name="title" maxlength="120" required placeholder="We chose pgvector"></div>
    <div class="field"><label for="nc">Note</label><textarea id="nc" name="content" rows="7" required placeholder="What was decided, and why."></textarea></div>
  </div><div class="modal-f"><button type="button" class="btn ghost" data-act="close">Cancel</button><button class="btn primary" type="submit">Save note</button></div></form>`);
  m.querySelector("form").dataset.slug = slug;
}
function openNewProject() {
  openModal(`${modalHead("New project")}<form data-form="project"><div class="modal-b">
    <p>Anyone in the team can start a project and becomes its owner. Add knowledge with <code>loom ingest .</code> or <code>loom github owner/repo</code>.</p>
    <p class="err" hidden></p>
    <div class="field"><label for="pn">Name</label><input id="pn" name="name" maxlength="80" required placeholder="Trip Photos"></div>
  </div><div class="modal-f"><button type="button" class="btn ghost" data-act="close">Cancel</button><button class="btn primary" type="submit">Create project</button></div></form>`);
}
function openInvite(slug = null) {
  const projects = S.team.projects.filter(canManage);
  const m = openModal(`${modalHead("Invite people")}<form data-form="invite"><div class="modal-b">
    <div class="seg" role="radiogroup" aria-label="Kind of invite">
      <label><input type="radio" name="kind" value="personal" checked><span>One person</span></label>
      <label><input type="radio" name="kind" value="link"><span>Team link for a group chat</span></label>
    </div>
    <p class="kind-help" data-for="personal">A personal invite works once, for 7 days. Whoever has it gets in straight away, so send it privately.</p>
    <p class="kind-help" data-for="link" hidden>One link for your team chat. People already in the team join the project at once; newcomers type their name and email, then wait until you let them in.</p>
    <p class="err" hidden></p>
    <div class="field"><label for="ip">Project</label><select id="ip" name="project">
      ${projects.map((p) => `<option value="${esc(p.slug)}" ${p.slug === slug ? "selected" : ""}>${esc(p.name)}</option>`).join("")}
      ${isAdmin() ? '<option value="">The whole team, no project yet</option>' : ""}
    </select></div>
    <div class="grid g2" data-for="personal">
      <div class="field"><label for="in">Name</label><input id="in" name="name" maxlength="80" placeholder="Sri"></div>
      <div class="field"><label for="ie">Email</label><input id="ie" name="email" type="email" placeholder="sri@example.com"><div class="hint">Only their identity in the team; nothing is emailed.</div></div>
    </div>
    <div class="grid g2">
      <div class="field"><label for="ir">Role</label><select id="ir" name="role">${Object.entries(ROLES).map(([r, label]) => `<option value="${r}" ${r === "member" ? "selected" : ""} ${r === "owner" ? 'data-for="personal"' : ""}>${label}</option>`).join("")}</select></div>
      <div class="field" data-for="link" hidden><label for="iu">People it lets in</label><input id="iu" name="uses" type="number" min="1" max="200" value="10"><div class="hint">Works for 2 days.</div></div>
    </div>
    <div data-slot="result"></div>
  </div><div class="modal-f"><button type="button" class="btn ghost" data-act="close">Close</button><button class="btn primary" type="submit">Create invite</button></div></form>`);
  return m;
}
function setInviteKind(form, kind) {
  for (const el of form.querySelectorAll("[data-for]")) el.hidden = el.dataset.for !== kind;
  if (kind === "link" && form.role.value === "owner") form.role.value = "member";
  form.querySelector('[data-slot="result"]').innerHTML = "";
  const submit = form.querySelector('button[type="submit"]');
  submit.hidden = false;
  submit.textContent = kind === "link" ? "Create team link" : "Create invite";
}
function inviteMessage(res, { kind, name, project }) {
  const team = S.team.team;
  const where = project ? ` (project ${project})` : "";
  const server = location.origin === HOSTED_SERVER || /^https?:\/\/(localhost|127\.)/.test(location.origin) ? "" : ` ${location.origin}`;
  const install = `curl -fsSL ${INSTALL_SCRIPT_URL} | sh -s -- ${res.code}${server}`;
  const join = `loom join ${res.code}${server ? ` --server${server}` : ""} && loom connect all`;
  const steps = `Paste this line into Terminal (Mac or Linux):\n\n${install}\n\nAlready have loom? Run this instead:\n${join}`;
  if (kind === "personal") {
    return `Hi ${name}! I've invited you to our LoomAI team "${team}"${where}.\n${steps}\n\nThe invite is just for you: it works once, for ${res.expires_in_days} days.`;
  }
  return `Join our LoomAI team "${team}"${where}!\n${steps}\n\n` +
    `New to the team? It asks for your name and email, then waits until ${S.team.me} lets you in. ` +
    `Already in the team? The loom join line adds you to the project straight away. ` +
    `Works for up to ${res.max_uses} new people for ${res.expires_in_days} days.`;
}

// ── Events ───────────────────────────────────────────────────────────────────
async function refreshAfterChange() {
  await Promise.all([loadTeam(), loadWaiting().catch(() => {}), loadInvites().catch(() => {})]);
  render();
}

document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-act]");
  if (!t) {
    if (ev.target.id === "overlay") closeModal();
    const row = ev.target.closest("[data-ev]");
    if (row && route().view === "activity" && S.feedTab === "log") openEvent(row.dataset.ev);
    return;
  }
  const act = t.dataset.act;
  if (["setrole", "filefilter"].includes(act)) return;
  if (act === "close") return closeModal();
  if (act === "filter") { S.feedFilter = t.dataset.k; location.hash = t.dataset.k === "all" ? "#/activity" : `#/activity/${t.dataset.k}`; return render(); }
  if (act === "who") { S.feedWho = S.feedWho === t.dataset.who ? null : t.dataset.who; return render(); }
  if (act === "tab") { ev.preventDefault(); S.feedTab = t.dataset.t; return render(); }
  if (act === "itab") { ev.preventDefault(); S.inboxTab = t.dataset.t; return render(); }
  if (act === "file") return openFile(t.dataset.slug, t.dataset.path);
  if (act === "note") return openNote(t.dataset.slug);
  if (act === "newproject") return openNewProject();
  if (act === "invite") { ev.preventDefault(); return openInvite(t.dataset.slug || (route().view === "p" ? route().a : null)); }
  if (act === "copy") { navigator.clipboard?.writeText(t.dataset.copy).then(() => toast("Copied"), () => toast("Couldn't copy", true)); return; }
  if (act === "copymessage") {
    const text = t.closest("[data-slot]").querySelector("textarea");
    try { await navigator.clipboard.writeText(text.value); } catch { text.select(); document.execCommand("copy"); }
    t.textContent = "Copied";
    return;
  }
  if (act === "revoke") {
    if (!confirm(`Revoke “${t.dataset.name}”? Every agent on that computer loses access immediately.`)) return;
    try { await api(`/api/devices/${encodeURIComponent(t.dataset.id)}/revoke`, { method: "POST" }); toast(`Revoked ${t.dataset.name}`); await loadTeam(); render(); }
    catch (e) { toast(e.message, true); }
    return;
  }
  if (act === "decide") {
    const letIn = t.dataset.verdict === "approve";
    if (letIn && !confirm(`Let ${t.dataset.name} in? They get access to the project straight away.`)) return;
    for (const b of t.parentElement.querySelectorAll("button")) b.disabled = true;
    try {
      await api(`/api/join/requests/${encodeURIComponent(t.dataset.id)}/${t.dataset.verdict}`, { method: "POST" });
      toast(letIn ? `${t.dataset.name} is in` : `Turned ${t.dataset.name} away`);
    } catch (e) { toast(e.message, true); }
    return refreshAfterChange();
  }
  if (act === "cancelinvite") {
    if (!confirm("Cancel this invite? It stops working right away; people who already joined stay.")) return;
    t.disabled = true;
    try { await api("/api/invites/cancel", { method: "POST", body: { id: t.dataset.id } }); toast("Invite cancelled"); }
    catch (e) { toast(e.message, true); }
    await loadInvites().catch(() => {});
    return render();
  }
  if (act === "removemember") {
    if (!confirm(`Remove ${t.dataset.name} from ${t.dataset.slug}? Their agents lose access to it immediately.`)) return;
    try { await api(`/api/projects/${encodeURIComponent(t.dataset.slug)}/members/${encodeURIComponent(t.dataset.email)}`, { method: "DELETE" }); toast(`Removed ${t.dataset.name}`); }
    catch (e) { toast(e.message, true); }
    return refreshAfterChange();
  }
  if (act === "accept" || act === "decline") {
    t.disabled = true;
    try {
      const item = await api(`/api/inbox/${encodeURIComponent(t.dataset.id)}/${act}`, { method: "POST" });
      await loadInbox();
      render();
      if (act === "accept") openModal(`${modalHead(`Message from ${esc(item.from)}`)}<div class="modal-b md">${md(item.content)}</div>`);
      else toast("Declined");
    } catch (e) { t.disabled = false; toast(e.message, true); }
  }
});

document.addEventListener("change", async (ev) => {
  const t = ev.target;
  if (t.name === "kind" && t.closest('[data-form="invite"]')) return setInviteKind(t.form, t.value);
  if (t.dataset.act === "setrole") {
    try {
      await api(`/api/projects/${encodeURIComponent(t.dataset.slug)}/members`, { method: "POST", body: { email: t.dataset.email, role: t.value } });
      toast(`Role changed to ${t.value}`);
    } catch (e) { toast(e.message, true); }
    return refreshAfterChange();
  }
});

document.addEventListener("submit", async (ev) => {
  const form = ev.target;
  ev.preventDefault();
  if (form.matches("[data-ask]")) return ask(form);
  const err = form.querySelector(".err");
  const fail = (m) => { if (err) { err.hidden = !m; err.textContent = m; } else if (m) toast(m, true); };
  const submit = form.querySelector('button[type="submit"]');
  fail("");
  if (submit) submit.disabled = true;
  try {
    if (form.dataset.form === "note") {
      const slug = form.dataset.slug;
      const res = await api(`/api/projects/${encodeURIComponent(slug)}/notes`, { method: "POST", body: { title: form.title.value.trim(), content: form.content.value } });
      closeModal();
      toast(`Saved as ${res.path}`);
      delete S.projects[slug];
      await loadTeam();
      render();
    }
    if (form.dataset.form === "project") {
      const project = await api("/api/projects", { method: "POST", body: { name: form.name.value.trim() } });
      closeModal();
      toast(`Created ${project.name ?? project.slug}`);
      await loadTeam();
      location.hash = `#/p/${encodeURIComponent(project.slug)}/people`;
    }
    if (form.dataset.form === "addmember") {
      await api(`/api/projects/${encodeURIComponent(form.dataset.slug)}/members`, { method: "POST", body: { email: form.email.value, role: form.role.value } });
      toast("Added to the project");
      await refreshAfterChange();
      return;
    }
    if (form.dataset.form === "invite") {
      const kind = form.kind.value;
      const personal = kind === "personal";
      const name = form.name.value.trim();
      const email = form.email.value.trim();
      if (personal && !(name && email)) { fail("Write their name and email, or choose a team link."); submit.disabled = false; return; }
      const project = form.project.value || null;
      const res = await api("/api/invites", { method: "POST", body: {
        project, role: form.role.value,
        email: personal ? email : null, display_name: personal ? name : null,
        max_uses: personal ? 1 : +form.uses.value || 10,
      } });
      form.querySelector('[data-slot="result"]').innerHTML = `
        <div class="invite-result">
          <div class="field"><label>${personal ? `Invite for ${esc(name)} is ready` : "Team link is ready"}</label>
            <textarea rows="9" readonly>${esc(inviteMessage(res, { kind, name, project }))}</textarea></div>
          <div class="row"><button type="button" class="btn primary sm" data-act="copymessage">Copy message</button>
            <span class="hint-line">${personal ? `Send it privately: whoever has this code gets in as ${esc(name)}.` : "Fine for a group chat: nobody new gets in until you let them in."} It's shown only once.</span></div>
        </div>`;
      submit.hidden = true;
      form.name.value = "";
      form.email.value = "";
      loadInvites().then(() => { if (route().view === "team") render(); }).catch(() => {});
      return;
    }
  } catch (e) { fail(e.message); }
  if (submit) submit.disabled = false;
});

document.addEventListener("input", (ev) => {
  if (ev.target.matches('[data-act="filefilter"]')) renderFiles(route().a, ev.target.value);
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && $("#overlay").classList.contains("open")) closeModal();
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k" && S.team) { ev.preventDefault(); openPalette(); }
  if (ev.key === "Enter" && ev.target.matches("tr[data-ev]")) openEvent(ev.target.dataset.ev);
});
function openPalette() {
  const r = route();
  const id = r.view === "p" ? `p:${r.a}` : "palette";
  openModal(`${modalHead("Ask the brain")}<div class="modal-b">${askBar(id, r.view === "p" ? { project: r.a } : {})}</div>`, { wide: true });
}
$("#searchBtn").addEventListener("click", openPalette);
$("#inviteBtn").addEventListener("click", () => openInvite(route().view === "p" ? route().a : null));
window.addEventListener("hashchange", () => {
  if (/login=/.test(location.hash)) { location.reload(); return; } // a fresh `loom dashboard` link opened in this tab
  closeModal(); render(); $("#main").scrollTop = 0;
});

// ── Live updates ─────────────────────────────────────────────────────────────
const refreshStats = debounce(() => loadStats().then(() => { renderChrome(); if (route().view === "home") liveRender(); }).catch(console.error), 700);
const refreshTeam = debounce(() => loadTeam().then(() => { if (["home", "team", "p"].includes(route().view)) liveRender(); }).catch(console.error), 700);
const refreshDoor = debounce(() => Promise.all([loadWaiting(), loadInvites()]).then(() => { renderChrome(); if (["home", "team"].includes(route().view)) liveRender(); }).catch(console.error), 500);
function liveRender() {
  if ($("#overlay").classList.contains("open") || document.activeElement?.matches("input, textarea, select")) { renderChrome(); return; }
  const main = $("#main"), top = main.scrollTop;
  render();
  main.scrollTop = top;
}
function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/api/ws?token=${encodeURIComponent(S.token)}&client=dashboard`);
  S.socket = socket;
  socket.onopen = () => { S.retry = 1000; setLive("open", "Live"); };
  socket.onmessage = (msg) => {
    let data;
    try { data = JSON.parse(msg.data); } catch { return; }
    if (data.type === "audit") {
      S.events.unshift(data.event);
      S.events.length = Math.min(S.events.length, MAX_EVENTS);
      S.fresh = data.event.id;
      if (["activity", "home"].includes(route().view)) liveRender();
      refreshStats();
      if (/^(device|family|member|project|ingest|note|join|agent\.cursor)/.test(data.event.action)) refreshTeam();
      if (/^(invite|family\.join|join|project\.join)/.test(data.event.action)) refreshDoor();
      if (/^guard\.rule/.test(data.event.action)) loadRules().then(() => { if (route().view === "rules") liveRender(); }).catch(console.error);
    } else if (data.type === "join.request") {
      toast(`${data.request?.name ?? "Someone"} is waiting at the door`);
      refreshDoor();
    } else if (data.type?.startsWith("delivery.")) {
      if (data.type === "delivery.new") toast(`${data.delivery?.from ?? "A teammate"} sent you ${data.delivery?.filename ?? "something"}`);
      loadInbox().then(() => { renderChrome(); if (["inbox", "home"].includes(route().view)) liveRender(); });
    } else if (data.type === "ingest.done") { toast(`Imported ${data.repo} into ${data.project}`); delete S.projects[data.project]; refreshTeam(); }
    else if (data.type === "ingest.failed") toast(`Import of ${data.repo} failed: ${data.reason}`, true);
    else if (data.type === "device.revoked") refreshTeam();
  };
  socket.onclose = (e) => {
    if (!S.token) return;
    if (e.code === 4401) { signOut("This dashboard session has ended. Open a new one from your terminal."); return; }
    setLive("closed", "Reconnecting");
    setTimeout(connect, S.retry);
    S.retry = Math.min(S.retry * 2, 15000);
  };
}

// ── Boot ─────────────────────────────────────────────────────────────────────
async function boot() {
  const login = location.hash.match(/login=([\w-]+)/);
  if (login) {
    history.replaceState(null, "", `${location.pathname}#/home`);
    try { S.token = await exchangeLoginCode(login[1]); write(TOKEN_KEY, S.token); }
    catch (e) { showGate(e.message); return; }
  }
  if (!S.token) { showGate(); return; }
  $("#gate").hidden = true;
  $("#app").hidden = false;
  try {
    await loadTeam();
    await Promise.all([loadMe().catch(() => {}), loadStats(), loadAudit(), loadInbox(),
      loadWaiting().catch(() => {}), loadInvites().catch(() => {}), loadRules().catch(() => {})]);
    render();
    connect();
  } catch (e) {
    if (e.message !== "unauthorized") showGate(`Couldn't reach the server: ${e.message}`);
  }
  setInterval(refreshTeam, 60000);
}
document.querySelectorAll("#gate [data-copy]").forEach((b) => b.setAttribute("data-act", "copy"));
boot();
