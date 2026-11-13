/* Run with Node: node web/frontend/tests_js/orders-badge.test.cjs */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../../static/js/orders-badge.js"), "utf8");

async function run() {
  let tick, interval, calls = [], count = 3, fail = false, redirected = false;
  const attrs = {};
  const badge = {dataset: {url: "/workspace/orders/count/"}, textContent: "",
    setAttribute: (k, v) => { attrs[k] = v; }, removeAttribute: k => { delete attrs[k]; }};
  const context = {document: {querySelector: () => badge},
    setInterval: (fn, ms) => { tick = fn; interval = ms; },
    fetch: async (url, options) => {
      calls.push({url, options});
      if (fail) throw new Error("offline");
      return {ok: true, redirected, json: async () => ({count})};
    }};
  vm.runInNewContext(source, context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  assert.equal(badge.textContent, "3");
  assert.equal(interval, 20000);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/workspace/orders/count/");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.headers, undefined);
  count = 0;
  await tick();
  assert.equal(badge.textContent, "0");
  fail = true;
  await tick();
  assert.equal(badge.textContent, "—");
  assert.equal(attrs["aria-label"], "Order count unavailable");
  fail = false; count = 2;
  await tick();
  assert.equal(badge.textContent, "2");
  assert.equal(attrs.title, undefined);
  redirected = true;
  await tick();
  assert.equal(badge.textContent, "—");
  vm.runInNewContext(source, {document: {querySelector: () => null},
    fetch: () => { throw new Error("Must not poll non-owner pages"); }});
  console.log("orders-badge: polling, counts, failure/recovery, session redirects, non-owner pages passed");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
