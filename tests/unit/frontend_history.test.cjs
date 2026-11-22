const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../frontend/app.js"), "utf8");

function browser(fetch) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) {
      const handlers = {};
      elements.set(id, {
        handlers,
        addEventListener(type, handler) {
          handlers[type] = handler;
        },
      });
    }
    return elements.get(id);
  };
  const context = vm.createContext({
    fetch,
    document: { getElementById: element },
    sessionStorage: { getItem() {} },
    // the page uses a request timeout, so the sandbox needs the same browser globals
    AbortController,
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(source, context);
  vm.runInContext('state.token = "first-token"', context);
  return { context, element };
}

function page(id, link = null) {
  const summary = { wild_monster: "Dewlet", team: ["Cinderpup"] };
  return { ok: true, status: 200, headers: { get: () => link }, text: async () => JSON.stringify([{ id, outcome: "win", summary }]) };
}

test("Older uses the cursor exactly and stops at the final page", async () => {
  const urls = [];
  const { context, element } = browser(async (url) => {
    urls.push(url);
    return urls.length === 1 ? page(2, '<?before=9007199254740993&limit=50>; rel="next"') : page(1);
  });
  await context.loadHistory();
  assert.equal(element("older-history").disabled, false);
  await vm.runInContext("loadHistory(historyBefore)", context);
  assert.match(urls[1], /\/history\?before=9007199254740993$/);
  assert.match(element("history").textContent, /^#1 /);
  assert.equal(element("older-history").disabled, true);
});

test("A pending response cannot restore another session's history", async () => {
  let resolve;
  const { context, element } = browser(() => new Promise((done) => { resolve = done; }));
  const old = context.loadHistory();
  vm.runInContext('clearHistory(); state.token = "second-token"', context);
  resolve(page(5));
  await old;
  assert.equal(element("history").textContent, "");
  assert.equal(element("older-history").disabled, true);
});

test("Clicking Older on the last page leaves the button disabled", async () => {
  const urls = [];
  const { context, element } = browser(async (url) => {
    urls.push(url);
    return urls.length === 1 ? page(2, '<?before=7&limit=50>; rel="next"') : page(1);
  });
  await context.loadHistory();
  assert.equal(element("older-history").disabled, false);
  await element("older-history").handlers.click(); // the real wiring, through once()
  assert.match(urls[1], /\/history\?before=7$/);
  assert.equal(element("older-history").disabled, true, "no older page is left");
  await element("older-history").handlers.click();
  assert.equal(urls.length, 3, "a disabled Older must not be able to restart from the newest page");
});
