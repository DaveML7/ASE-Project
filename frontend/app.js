// Minimal Monster Tamers client. No game logic here: every rule lives in the backend.
const API_BASE = "https://localhost:8443/api/v1";
const state = { token: sessionStorage.getItem("token") || "" };
let historyRequest = 0;
let historyBefore = "";

const $ = (id) => document.getElementById(id);
const status = (message, error = true) => {
  $("status").textContent = message;
  $("status").className = error ? "error" : "muted";
};

const REQUEST_TIMEOUT_MS = 15000;

async function api(path, { method = "GET", body, withHeaders = false } = {}) {
  const token = state.token;
  // A request that never answers must not freeze the page: cut it after a timeout.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (error) {
    throw new Error(
      error.name === "AbortError" ? "The server did not answer in time." : "Cannot reach the server.",
    );
  } finally {
    clearTimeout(timer);
  }
  if (response.status === 401 && token === state.token) {
    state.token = "";
    sessionStorage.removeItem("token");
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // an HTML error page from a proxy: fall back to the HTTP status below
  }
  if (!response.ok) throw new Error(data?.message || `HTTP ${response.status}`);
  return withHeaders ? { data, headers: response.headers } : data;
}

// One request per action at a time: a double click must not start two encounters.
// Buttons that manage their own state (pagination) opt out of the automatic re-enabling.
const busy = new Set();
function once(name, action, { manageButton = false } = {}) {
  return async (...args) => {
    if (busy.has(name)) return;
    busy.add(name);
    const button = $(name);
    if (button && !manageButton) button.disabled = true;
    try {
      await action(...args);
    } finally {
      busy.delete(name);
      if (button && !manageButton) button.disabled = false;
    }
  };
}

const imageUrl = (path) => (path ? `${API_BASE}${path}` : "");

async function login(register) {
  const username = $("username").value.trim();
  const password = $("password").value;
  try {
    const path = register ? "/auth/register" : "/auth/login";
    const data = await api(path, { method: "POST", body: { username, password } });
    clearHistory();
    state.token = data.access_token;
    sessionStorage.setItem("token", state.token);
    status(register ? "Account created." : "Logged in.", false);
    await loadPlayer();
  } catch (error) {
    status(error.message);
  }
}

async function logout() {
  // Revoke the token on the server (not just forget it), so a copied token stops working.
  if (state.token) {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch (error) {
      // already expired/revoked: nothing left to revoke
    }
  }
  state.token = "";
  sessionStorage.removeItem("token");
  clearHistory();
  showSections(false);
  $("result-section").hidden = true;
  cache.collection = [];
  status("Logged out.", false);
}

// ---- small DOM helpers (text only: nothing from the server is ever parsed as HTML)
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const SECTIONS = ["player", "team", "encounter", "collection", "history", "catalogue"];
const showSections = (visible) => {
  for (const id of SECTIONS) $(`${id}-section`).hidden = !visible;
  $("auth-section").hidden = visible;
};

// ---- data: static things are fetched once per session, the rest only when it can have changed
const cache = { catalogue: null, templates: null, collection: [], teams: [] };

async function loadStatic() {
  // The catalogue and the encounter list rarely change: the server marks them cacheable
  // (ETag), so after the first visit the browser revalidates with a tiny 304.
  const [catalogue, templates] = await Promise.all([api("/monsters"), api("/encounters")]);
  cache.catalogue = catalogue;
  cache.templates = templates;
  $("catalogue").replaceChildren(...catalogue.map(renderMonster));
  $("encounter-select").replaceChildren(
    ...templates.map(
      (e) => new Option(`${e.name}: ${e.wild_monster?.name ?? "unknown monster"}, difficulty ${e.difficulty}`, e.id),
    ),
  );
}

async function loadProfile() {
  const profile = await api("/players/me");
  const name = el("div", "name", profile.username);
  const meta = el("p", "meta");
  meta.append(el("span", "level", `Level ${profile.level}`), `${profile.xp} XP · ${profile.monsters_owned} monsters`);
  $("profile").replaceChildren(name, meta);
}

async function loadCollection() {
  cache.collection = await api("/players/me/monsters");
  $("collection").replaceChildren(...cache.collection.map(renderMonster));
}

async function loadTeams() {
  cache.teams = await api("/teams");
  $("teams").textContent = cache.teams.length
    ? cache.teams.map((t) => `${t.name} (${t.member_ids.length}/${t.max_size})`).join(" · ")
    : "No team yet: create one to start an encounter.";
  $("team-select").replaceChildren(
    ...cache.teams.map((team) => new Option(`${team.name} (#${team.id})`, team.id)),
  );
}

// Reload only what an action can have changed (a request costs time, bandwidth and server work).
async function refresh({ profile = false, collection = false, teams = false, history = false } = {}) {
  try {
    await Promise.all([
      profile && loadProfile(),
      collection && loadCollection(),
      teams && loadTeams(),
      history && loadHistory(),
    ]);
  } catch (error) {
    status(error.message);
  }
}

async function loadPlayer() {
  if (!state.token) return;
  try {
    await Promise.all([loadStatic(), loadProfile(), loadCollection(), loadTeams(), loadHistory()]);
    showSections(true);
  } catch (error) {
    status(error.message);
    if (!state.token) logout();
  }
}

function renderMonster(monster) {
  const card = el("div", "card");
  card.dataset.element = monster.element;
  const img = el("img");
  img.src = imageUrl(monster.image_url);
  img.alt = `${monster.name}`;
  img.loading = "lazy";
  img.width = img.height = 52;
  const text = el("div");
  text.append(
    el("div", "title", monster.name),
    el(
      "div",
      "stats",
      `${monster.element} · HP ${monster.hp} · ATK ${monster.attack} · DEF ${monster.defence}` +
        (monster.level ? ` · Level ${monster.level}` : ""),
    ),
  );
  card.append(img, text);
  return card;
}

async function createTeam() {
  try {
    // The collection is already on the page: no request needed to pick the first three.
    const memberIds = cache.collection.slice(0, 3).map((entry) => entry.player_monster_id);
    const name = $("team-name").value.trim() || "Team";
    await api("/teams", { method: "POST", body: { name, member_ids: memberIds } });
    status("Team created.", false);
    await refresh({ teams: true });
  } catch (error) {
    status(error.message);
  }
}

// The encounter ticket: your team's power against what the wild monster demands.
function renderResult(encounter) {
  const { outcome, details, progress } = encounter;
  const { score, threshold, team_power: teamPower } = details.resolution;
  const peak = Math.max(score, threshold, teamPower, 1);
  const won = outcome === "win";

  const head = el("div", "ticket-head");
  head.append(
    el("span", `verdict ${outcome}`, won ? "Victory" : "Defeat"),
    el("span", "vs", `against ${details.wild_monster.name}`),
  );

  const bar = (label, value, className) => {
    const line = el("div", "bar-label");
    line.append(el("span", "", label), el("span", "", String(value)));
    const track = el("div", `bar ${className}`);
    const fill = el("span");
    fill.style.width = `${Math.round((value / peak) * 100)}%`;
    track.append(fill);
    const wrap = el("div");
    wrap.append(line, track);
    return wrap;
  };
  const bars = el("div", "bars");
  bars.append(
    bar("Your score", score, won ? "score" : "score lost"),
    bar("Needed to win", threshold, "threshold"),
  );

  const reward = details.reward ? `New monster: ${details.reward.name}.` : "No new monster this time.";
  const foot = el("p", "ticket-foot", `+${progress.xp_gained} XP, now level ${progress.level}. ${reward}`);
  $("encounter-result").replaceChildren(head, bars, foot);
  $("result-section").hidden = false;
}

async function startEncounter() {
  try {
    const teamId = Number($("team-select").value);
    const templateId = Number($("encounter-select").value);
    if (!teamId) throw new Error("Create a team first.");
    const encounter = await api("/encounters", {
      method: "POST",
      body: { team_id: teamId, template_id: templateId },
    });
    renderResult(encounter);
    // Teams and the catalogue did not change: refresh the rest, the collection only after a reward.
    await refresh({ profile: true, history: true, collection: Boolean(encounter.details.reward) });
  } catch (error) {
    status(error.message);
  }
}

function clearHistory() {
  historyRequest++;
  historyBefore = "";
  $("history").textContent = "";
  $("older-history").disabled = true;
}

async function loadHistory(before = "") {
  const current = ++historyRequest;
  const token = state.token;
  $("older-history").disabled = true;
  try {
    const query = before ? `?before=${before}` : "";
    const { data: history, headers } = await api(`/history${query}`, { withHeaders: true });
    if (current !== historyRequest || token !== state.token) return;
    historyBefore = headers.get("Link")?.match(/[?&]before=([0-9]{1,19})(?:&|>)/)?.[1] || "";
    $("history").textContent = history.length
      ? history
          .map(
            (entry) =>
              `#${entry.id} ${entry.outcome} vs ${entry.summary.wild_monster || "unknown monster"} ` +
              `(team: ${entry.summary.team.join(", ")}) · reward: ${entry.summary.reward || "none"}`,
          )
          .join("\n")
      : before ? "No older encounters." : "No encounters yet.";
  } catch (error) {
    if (current === historyRequest) {
      if (!state.token) clearHistory();
      status(error.message);
    }
  } finally {
    if (current === historyRequest) $("older-history").disabled = !historyBefore;
  }
}

$("api-base-label").textContent = API_BASE;
$("login").addEventListener("click", once("login", () => login(false)));
$("register").addEventListener("click", once("register", () => login(true)));
$("logout").addEventListener("click", once("logout", logout));
$("create-team").addEventListener("click", once("create-team", createTeam));
$("start-encounter").addEventListener("click", once("start-encounter", startEncounter));
$("refresh-history").addEventListener("click", once("refresh-history", () => loadHistory()));
// loadHistory enables or disables "Older" itself: it must not be forced back on afterwards.
$("older-history").addEventListener(
  "click",
  once("older-history", () => loadHistory(historyBefore), { manageButton: true }),
);
if (state.token) loadPlayer();
