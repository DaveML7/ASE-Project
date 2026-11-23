const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../frontend/app.js"), "utf8");

// A just-enough DOM: elements remember their children and text, nothing is parsed as HTML.
function node(tag = "div") {
  const n = {
    tag, children: [], dataset: {}, style: {}, className: "", hidden: false, disabled: false,
    value: "", handlers: {}, _text: "",
    addEventListener(type, handler) { this.handlers[type] = handler; },
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    get textContent() { return this._text + this.children.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); },
    set textContent(value) { this._text = value; this.children = []; },
  };
  return n;
}

function browser(routes) {
  const elements = new Map();
  const requests = [];
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, node());
    return elements.get(id);
  };
  const fetch = async (url, options = {}) => {
    const route = url.replace("https://localhost:8443/api/v1", "");
    requests.push(`${options.method || "GET"} ${route}`);
    const body = routes[`${options.method || "GET"} ${route}`] ?? routes[route.split("?")[0]];
    return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body) };
  };
  const context = vm.createContext({
    fetch,
    document: { getElementById: element, createElement: node },
    sessionStorage: { getItem: () => "token", setItem() {}, removeItem() {} },
    Option: function Option(text, value) { return { text, value }; },
    AbortController, setTimeout, clearTimeout,
  });
  vm.runInContext(source, context); // the script boots itself: a stored token triggers loadPlayer()
  return { context, element, requests, ready: () => new Promise((done) => setTimeout(done, 20)) };
}

const monster = (id, element = "fire") => ({
  id, player_monster_id: id, name: `M${id}`, element, hp: 10, attack: 5, defence: 3, image_url: `/monsters/${id}/image`,
});
const encounter = (reward) => ({
  id: 9, outcome: "win",
  details: { wild_monster: { name: "Dewlet" }, resolution: { score: 80, threshold: 50, team_power: 70 }, ...(reward ? { reward: { name: "Flameox" } } : {}) },
  progress: { xp_gained: 20, xp: 40, level: 1 },
});

const routes = (reward) => ({
  "/players/me": { username: "ash", level: 1, xp: 20, monsters_owned: 3 },
  "/players/me/monsters": [monster(1), monster(2), monster(3)],
  "/monsters": [monster(1), monster(2)],
  "/teams": [{ id: 4, name: "A", member_ids: [1, 2, 3], max_size: 3 }],
  "/encounters": [{ id: 1, name: "Forest", wild_monster: { name: "Sproutle" }, difficulty: 1 }],
  "/history": [],
  "POST /encounters": encounter(reward),
  "POST /teams": { id: 5 },
});

test("the first load fetches everything once and shows the game", async () => {
  const { element, requests, ready } = browser(routes(false));
  await ready();
  assert.deepEqual([...requests].sort(), ["GET /encounters", "GET /history", "GET /monsters", "GET /players/me", "GET /players/me/monsters", "GET /teams"]);
  assert.equal(element("auth-section").hidden, true);
  assert.equal(element("collection-section").hidden, false);
  assert.match(element("profile").textContent, /ash/);
});

test("after an encounter only the progress and the history are reloaded", async () => {
  const { context, element, requests, ready } = browser(routes(false));
  await ready();
  requests.length = 0;
  element("team-select").value = "4";
  element("encounter-select").value = "1";
  await vm.runInContext("startEncounter()", context);
  assert.deepEqual([...requests].sort(), ["GET /history", "GET /players/me", "POST /encounters"]);
  assert.equal(element("result-section").hidden, false);
  assert.match(element("encounter-result").textContent, /Victory/);
  assert.match(element("encounter-result").textContent, /\+20 XP/);
});

test("a reward also reloads the collection, never the catalogue or the teams", async () => {
  const { context, element, requests, ready } = browser(routes(true));
  await ready();
  requests.length = 0;
  element("team-select").value = "4";
  await vm.runInContext("startEncounter()", context);
  assert.deepEqual([...requests].sort(), ["GET /history", "GET /players/me", "GET /players/me/monsters", "POST /encounters"]);
  assert.match(element("encounter-result").textContent, /Flameox/);
});

test("creating a team reuses the collection already on the page", async () => {
  const { context, element, requests, ready } = browser(routes(false));
  await ready();
  requests.length = 0;
  await vm.runInContext("createTeam()", context);
  assert.deepEqual([...requests].sort(), ["GET /teams", "POST /teams"]);
});

test("starting an encounter without a team explains what to do", async () => {
  const data = routes(false);
  data["/teams"] = [];
  const { context, element, requests, ready } = browser(data);
  await ready();
  requests.length = 0;
  await vm.runInContext("startEncounter()", context);
  assert.equal(requests.length, 0, "no request without a team");
  assert.match(element("status").textContent, /Create a team first/);
});

test("monster cards carry their element and never parse server text as HTML", () => {
  const { context } = browser(routes(false));
  const card = vm.runInContext('renderMonster({name: "<img onerror=x>", element: "water", hp: 1, attack: 2, defence: 3, image_url: "/m"})', context);
  assert.equal(card.dataset.element, "water");
  assert.match(card.textContent, /<img onerror=x>/);
  assert.equal(card.children.filter((c) => c.tag === "img").length, 1);
});
