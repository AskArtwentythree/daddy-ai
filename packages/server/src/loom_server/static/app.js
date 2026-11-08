const TOKEN_KEY = "loom.dashboard.token";
const MAX_EVENTS = 250;

const AGENTS = {
  "cursor-vscode": "Cursor",
  cursor: "Cursor",
  "claude-code": "Claude Code",
  "claude-ai": "Claude",
  "loom-cli": "Terminal",
  "loom-watch": "Watcher",
  "daddy-cli": "Terminal",
  "daddy-watch": "Watcher",
  "cursor-hook": "Cursor",
  "claude-hook": "Claude Code",
  dashboard: "Dashboard",
  "mcp-smoke": "Test client",
  "mcp-client": "MCP client",
};

const FILTERS = {
  all: () => true,
  stopped: (e) => e.decision !== "allow",
  deliveries: (e) => e.action.startsWith("delivery.") || e.action === "agent.share_blocked",
  knowledge: (e) => /^(knowledge|note|brief|files?|ingest)\./.test(e.action),
};

const state = {
  token: sessionStorage.getItem(TOKEN_KEY),
  isAdmin: false,
  events: [],
  filter: "all",
  socket: null,
  retry: 1000,
};

const $ = (selector) => document.querySelector(selector);

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const clip = (value, n) => {
  const text = String(value ?? "");
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
};

const agentName = (client) => AGENTS[client] ?? client ?? "";

function debounce(fn, ms) {
  let timer;
  return () => {
    clearTimeout(timer);
    timer = setTimeout(fn, ms);
  };
}

// ── API ──────────────────────────────────────────────────────────────────────

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${state.token}`,
      "X-Loom-Client": "dashboard",
    },
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) {
    signOut("This dashboard session has ended. Open a new one from your terminal:");
    throw new Error("unauthorized");
  }
  if (!response.ok) throw new Error(body.reason || response.statusText);
  return body;
}

async function exchangeLoginCode(code) {
  const response = await fetch("/api/dashboard/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.reason || "This link no longer works.");
  return body.token;
}

// ── Gate ─────────────────────────────────────────────────────────────────────

function showGate(message) {
  $("#app").hidden = true;
  $("#gate").hidden = false;
  if (message) $("#gate-message").textContent = message;
}

function signOut(message) {
  sessionStorage.removeItem(TOKEN_KEY);
  state.token = null;
  state.socket?.close();
  showGate(message);
}

// ── Figures ──────────────────────────────────────────────────────────────────

function sparkline(svg, series) {
  const max = Math.max(1, ...series);
  const step = 120 / (series.length - 1);
  const points = series.map((v, i) => `${(i * step).toFixed(1)},${(26 - (v / max) * 24).toFixed(1)}`).join(" ");
  svg.innerHTML = `<line x1="0" y1="27.5" x2="120" y2="27.5"></line><polyline points="${points}"></polyline>`;
  svg.classList.toggle("quiet", series.every((v) => v === 0));
}

async function loadStats() {
  if (!state.isAdmin) return;
  const stats = await api("/api/stats");
  for (const key of ["questions", "delivered", "stopped"]) {
    $(`#n-${key}`).textContent = stats[key].total.toLocaleString();
    sparkline($(`#s-${key}`), stats[key].series);
  }
  $("#n-agents").textContent = stats.agents.toLocaleString();
}

// ── Family tree ──────────────────────────────────────────────────────────────

function relativeTime(iso) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

function deviceItem(device) {
  const status = device.revoked_at ? "dot-off" : device.online ? "dot-on" : "";
  const agents = device.name === "Dashboard" ? "this ledger" : [...new Set(device.agents.map(agentName))].join(" · ");
  const seen = device.revoked_at ? "revoked" : device.online ? "online now" : `seen ${relativeTime(device.last_seen_at)}`;
  const canRevoke = state.isAdmin && !device.revoked_at;
  return `
    <li class="device ${device.revoked_at ? "revoked" : ""}">
      <div class="device-name" title="${esc(device.name)}">
        <i class="dot ${status}"></i><span>${esc(device.name)}</span>
        ${canRevoke ? `<button class="revoke" data-device="${esc(device.id)}" data-name="${esc(device.name)}">Revoke</button>` : ""}
      </div>
      <p class="agents">${esc(agents)}</p>
      <p class="device-meta">${esc(seen)}</p>
    </li>`;
}

function renderTree(data) {
  const isFormer = (m) => m.devices.length > 0 && m.devices.every((d) => d.revoked_at);
  const former = data.members.filter(isFormer);
  const members = data.members
    .filter((m) => !isFormer(m))
    .map((member) => {
      const projects = member.projects.length;
      return `
        <li class="member">
          <div class="person">
            <span class="role">${member.is_admin ? "Admin" : "Member"}</span>
            <strong>${esc(member.name)}</strong>
            <span>${projects} project${projects === 1 ? "" : "s"}</span>
          </div>
          <ul class="devices">${member.devices.map(deviceItem).join("")}</ul>
        </li>`;
    })
    .join("");
  $("#tree").innerHTML = `
    <div class="root">
      <span class="seal">D</span>
      <strong>LoomAI</strong>
      <span class="root-note">memory · policy · delivery</span>
    </div>
    <ul class="branches">${members}</ul>
    ${former.length ? `<p class="former">Revoked: ${former.map((m) => `<s>${esc(m.name)}</s>`).join(", ")}</p>` : ""}`;
}

function renderProjects(projects) {
  $("#projects").innerHTML =
    projects
      .map(
        (p) => `
      <tr>
        <td><strong>${esc(p.name)}</strong><small>${esc(p.slug)}</small></td>
        <td>${esc(p.my_role ?? "—")}</td>
        <td class="num">${p.members}</td>
        <td class="num">${p.documents.toLocaleString()}</td>
        <td class="num">${p.chunks.toLocaleString()}</td>
      </tr>`,
      )
      .join("") || `<tr><td colspan="5" class="empty">No projects yet.</td></tr>`;
}

async function loadFamily() {
  const data = await api("/api/family");
  state.isAdmin = data.is_admin;
  $("#family-name").textContent = data.family;
  $("#me").textContent = data.me;
  const p = data.providers;
  $("#models").innerHTML = `Embeddings <code>${esc(p.embedder)}</code>, reranker <code>${esc(p.reranker)}</code>, answers <code>${esc(p.llm)}</code>.`;
  api("/api/guard/rules")
    .then((rules) => {
      const team = rules.family.length ? `, plus ${rules.family.length} of the team's own` : "";
      $("#guard").textContent =
        `Every shell command, file read and tool call an agent makes is checked first: ${rules.builtin.length} built-in rules${team}. ` +
        "Secrets and credential files are always blocked.";
    })
    .catch(console.error);
  renderTree(data);
  renderProjects(data.projects);
}

// ── Ledger ───────────────────────────────────────────────────────────────────

function isNoise(event) {
  if (event.action === "agent.cursor.stop" || event.action === "dashboard.login") return true;
  return event.client === "dashboard" && /^device\.(online|offline)$/.test(event.action);
}

function describe(event) {
  const d = event.detail || {};
  const ok = event.decision === "allow";
  const quote = (text) => `<q>${esc(clip(text, 110))}</q>`;
  const file = (name) => `<i>${esc(name)}</i>`;
  switch (event.action) {
    case "knowledge.ask":
      return `${ok ? "asked" : "tried to ask"} ${quote(d.question)}`;
    case "knowledge.search":
      return `${ok ? "searched for" : "tried to search for"} ${quote(d.query)}`;
    case "delivery.send":
      return `${ok ? "sent" : "tried to send"} ${file(d.filename)} to <b>${esc(d.to)}</b>`;
    case "delivery.accept":
      return `accepted ${file(d.filename ?? "a delivery")}${d.sender ? ` from <b>${esc(d.sender)}</b>` : ""}`;
    case "delivery.decline":
      return "declined a delivery";
    case "agent.share_blocked":
      return `was stopped on the laptop before sending ${file(d.filename)} to <b>${esc(d.to)}</b>`;
    case "agent.prompt_blocked":
      return "was stopped from putting a secret into a prompt";
    case "agent.shell":
    case "agent.mcp": {
      const what = event.action === "agent.shell" ? `<code>${esc(clip(d.target, 90))}</code>` : `the tool ${file(d.target)}`;
      if (d.verdict === "ask") return `had to ask before running ${what}`;
      return ok ? `ran ${what}` : `was stopped from running ${what}`;
    }
    case "agent.guard_blocked": {
      const verb = { shell: "running", read: "reading", mcp: "calling" }[d.kind] ?? "doing";
      const what = d.kind === "shell" ? `<code>${esc(clip(d.target, 90))}</code>` : file(d.target);
      return `was stopped on the laptop from ${verb} ${what}`;
    }
    case "guard.rule_add":
      return `${ok ? "added" : "tried to add"} the guard rule <code>${esc(d.pattern)}</code> (${esc(d.verdict)} ${esc(d.kind)})`;
    case "guard.rule_remove":
      return "removed a guard rule";
    case "agent.cursor.after_mcp_execution":
      return `used the tool ${file(d.tool_name)}${d.mcp_server_name ? ` from ${esc(d.mcp_server_name)}` : ""}`;
    case "note.save":
      return `${ok ? "saved the note" : "tried to save the note"} ${quote(d.title)}`;
    case "brief.read":
      return "read the project brief";
    case "files.list":
      return "listed the project's files";
    case "file.read":
      return `opened ${file(d.path)}`;
    case "ingest.upload":
      return `added ${d.indexed ?? 0} file${d.indexed === 1 ? "" : "s"} to project knowledge`;
    case "ingest.github":
    case "ingest.github.request":
      return `imported ${file(d.repo)} from GitHub`;
    case "invite.create":
      return `invited ${esc(d.email)}`;
    case "family.join":
      return `${esc(d.email)} joined the family`;
    case "family.create":
      return "founded the family";
    case "project.create":
      return `created the project ${file(d.slug)}`;
    case "member.add":
      return `added ${esc(d.email)} as ${esc(d.role)}`;
    case "member.remove":
      return `removed ${esc(d.email)} from the project`;
    case "device.online":
      return "came online";
    case "device.offline":
      return "went offline";
    case "device.revoke":
      return "revoked a computer";
    case "dashboard.login":
      return "opened the dashboard";
    case "user.rename":
      return ok ? `changed their name from <b>${esc(d.old)}</b> to <b>${esc(d.new)}</b>` : `tried to change their name to <b>${esc(d.new)}</b>`;
    default:
      return esc(event.action);
  }
}

function entryHtml(event, fresh) {
  const time = new Date(event.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const who = event.user ?? "LoomAI";
  const agent = agentName(event.client) || event.device || "";
  return `
    <li class="entry ${esc(event.decision)} ${fresh ? "fresh" : ""}">
      <time datetime="${esc(event.at)}">${time}</time>
      <div class="who"><b>${esc(who)}</b><small>${esc(agent)}</small></div>
      <div class="what">
        ${describe(event)}${event.project ? `<span class="project">${esc(event.project)}</span>` : ""}
        ${event.reason && event.decision !== "allow" ? `<p class="reason">${esc(event.reason)}</p>` : ""}
      </div>
      <span class="verdict" aria-label="${esc(event.decision)}">${event.decision === "allow" ? "✓" : "✕"}</span>
    </li>`;
}

function renderLedger(freshId) {
  const meaningful = state.events.filter((e) => !isNoise(e));
  for (const button of document.querySelectorAll("#filters button")) {
    const n = meaningful.filter(FILTERS[button.dataset.filter]).length;
    const count = button.querySelector(".count");
    count.textContent = n ? n.toLocaleString() : "";
    count.classList.toggle("alarm", button.dataset.filter === "stopped" && n > 0);
  }
  const visible = meaningful.filter(FILTERS[state.filter]);
  $("#ledger").innerHTML = visible.map((e) => entryHtml(e, e.id === freshId)).join("");
  $("#ledger-empty").hidden = visible.length > 0;
}

async function loadLedger() {
  if (!state.isAdmin) {
    $("#ledger-empty").hidden = false;
    $("#ledger-empty").textContent = "Only family admins can read the ledger.";
    return;
  }
  const events = await api("/api/audit?limit=200");
  state.events = events.reverse();
  renderLedger();
}

// ── Live updates ─────────────────────────────────────────────────────────────

const refreshFigures = debounce(() => loadStats().catch(console.error), 700);
const refreshFamily = debounce(() => loadFamily().catch(console.error), 700);

function setLive(stateName, label) {
  const el = $("#live");
  el.dataset.state = stateName;
  el.textContent = label;
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${location.host}/api/ws?token=${encodeURIComponent(state.token)}&client=dashboard`);
  state.socket = socket;
  socket.onopen = () => {
    state.retry = 1000;
    setLive("open", "Live");
  };
  socket.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.type !== "audit") return;
    const entry = event.event;
    state.events.unshift(entry);
    state.events.length = Math.min(state.events.length, MAX_EVENTS);
    renderLedger(entry.id);
    refreshFigures();
    if (/^(device|family|member|project|ingest|note|dashboard|agent\.cursor)/.test(entry.action)) refreshFamily();
  };
  socket.onclose = (closeEvent) => {
    if (!state.token) return;
    if (closeEvent.code === 4401) {
      signOut("This dashboard session has ended. Open a new one from your terminal:");
      return;
    }
    setLive("closed", "Reconnecting");
    setTimeout(connect, state.retry);
    state.retry = Math.min(state.retry * 2, 15000);
  };
}

// ── Wiring ───────────────────────────────────────────────────────────────────

$("#filters").addEventListener("click", (click) => {
  const button = click.target.closest("button[data-filter]");
  if (!button) return;
  state.filter = button.dataset.filter;
  for (const b of document.querySelectorAll("#filters button")) b.setAttribute("aria-pressed", String(b === button));
  $("#ledger-empty").textContent = state.filter === "stopped" ? "Nothing stopped. All clear." : "Nothing here yet. Ask your agent something.";
  renderLedger();
});

$("#tree").addEventListener("click", async (click) => {
  const button = click.target.closest("button.revoke");
  if (!button) return;
  if (!confirm(`Revoke “${button.dataset.name}”? Every agent on that computer loses access immediately.`)) return;
  try {
    await api(`/api/devices/${button.dataset.device}/revoke`, { method: "POST" });
    await loadFamily();
  } catch (error) {
    alert(error.message);
  }
});

async function boot() {
  const login = location.hash.match(/login=([\w-]+)/);
  if (login) {
    history.replaceState(null, "", location.pathname);
    try {
      state.token = await exchangeLoginCode(login[1]);
      sessionStorage.setItem(TOKEN_KEY, state.token);
    } catch (error) {
      showGate(`${error.message}`);
      return;
    }
  }
  if (!state.token) {
    showGate();
    return;
  }
  $("#today").textContent = new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  $("#gate").hidden = true;
  $("#app").hidden = false;
  try {
    await loadFamily();
    await Promise.all([loadStats(), loadLedger()]);
    connect();
  } catch (error) {
    if (error.message !== "unauthorized") showGate(`Could not reach LoomAI: ${error.message}`);
  }
  setInterval(() => refreshFamily(), 60000);
}

boot();
