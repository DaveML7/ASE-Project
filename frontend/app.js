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

async function api(path, { method = "GET", body, withHeaders = false } = {}) {
  const token = state.token;
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 401 && token === state.token) {
    state.token = "";
    sessionStorage.removeItem("token");
  }
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(data?.message || `HTTP ${response.status}`);
  return withHeaders ? { data, headers: response.headers } : data;
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
  for (const id of ["player", "catalogue", "collection", "team", "encounter", "history"]) {
    $(`${id}-section`).hidden = true;
  }
  status("Logged out.", false);
}

async function loadPlayer() {
  if (!state.token) return;
  try {
    const [profile, collection, catalogue, teams, encounters] = await Promise.all([
      api("/players/me"),
      api("/players/me/monsters"),
      api("/monsters"),
      api("/teams"),
      api("/encounters"),
    ]);
    $("profile").textContent =
      `${profile.username} · level ${profile.level} · ${profile.xp} XP · ` +
      `${profile.monsters_owned} monsters · avatar ${profile.avatar}`;

    $("catalogue").replaceChildren(...catalogue.map(renderMonster));
    $("collection").replaceChildren(...collection.map(renderMonster));
    $("teams").textContent = teams.length
      ? teams.map((t) => `${t.name} (${t.member_ids.length}/${t.max_size})`).join(" · ")
      : "No team yet.";

    $("team-select").replaceChildren(
      ...teams.map((team) => new Option(`${team.name} (#${team.id})`, team.id)),
    );
    $("encounter-select").replaceChildren(
      ...encounters.map(
        (e) => new Option(`${e.name} · ${e.wild_monster.name} (diff ${e.difficulty})`, e.id),
      ),
    );
    for (const id of ["player", "catalogue", "collection", "team", "encounter", "history"]) {
      $(`${id}-section`).hidden = false;
    }
    await loadHistory();
  } catch (error) {
    status(error.message);
    if (!state.token) logout();
  }
}

function renderMonster(monster) {
  const card = document.createElement("div");
  card.className = "card";
  const img = document.createElement("img");
  img.src = imageUrl(monster.image_url);
  img.alt = `${monster.name} artwork`;
  const text = document.createElement("div");
  text.innerHTML = `<strong></strong><div class="muted"></div>`;
  text.querySelector("strong").textContent = monster.name;
  text.querySelector(".muted").textContent =
    `${monster.element} · HP ${monster.hp} · ATK ${monster.attack} · DEF ${monster.defence}` +
    (monster.level ? ` · Lv ${monster.level}` : "");
  card.append(img, text);
  return card;
}

async function createTeam() {
  try {
    const collection = await api("/players/me/monsters");
    const memberIds = collection.slice(0, 3).map((entry) => entry.player_monster_id);
    const name = $("team-name").value.trim() || "Team";
    await api("/teams", { method: "POST", body: { name, member_ids: memberIds } });
    status("Team created.", false);
    await loadPlayer();
  } catch (error) {
    status(error.message);
  }
}

async function startEncounter() {
  try {
    const teamId = Number($("team-select").value);
    const templateId = Number($("encounter-select").value);
    if (!teamId) throw new Error("Create a team first");
    const encounter = await api("/encounters", {
      method: "POST",
      body: { team_id: teamId, template_id: templateId },
    });
    const { outcome, details } = encounter;
    $("encounter-result").textContent = JSON.stringify(
      {
        outcome,
        wild: details.wild_monster.name,
        score: details.resolution.score,
        threshold: details.resolution.threshold,
        reward: details.reward ? details.reward.name : "none",
      },
      null,
      2,
    );
    await loadPlayer();
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
$("login").addEventListener("click", () => login(false));
$("register").addEventListener("click", () => login(true));
$("logout").addEventListener("click", logout);
$("create-team").addEventListener("click", createTeam);
$("start-encounter").addEventListener("click", startEncounter);
$("refresh-history").addEventListener("click", () => loadHistory());
$("older-history").addEventListener("click", () => loadHistory(historyBefore));
if (state.token) loadPlayer();
