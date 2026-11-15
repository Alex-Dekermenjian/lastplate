/* Run with Node: node web/frontend/tests_js/countdown.test.cjs */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../../static/js/countdown.js"), "utf8");

function element(attrs, hidden) {
  const classes = new Set();
  return {hidden, textContent: "", attrs: {...attrs},
    getAttribute(k) { return this.attrs[k]; }, removeAttribute(k) { delete this.attrs[k]; },
    classList: {add: c => classes.add(c), has: c => classes.has(c)}};
}

let now = Date.parse("2026-11-19T17:00:00Z");
let tick;
const countdown = element({"data-deadline": "2026-11-19T17:30:30Z"});
const cancel = element({}, false);
const phone = element({}, true);
const context = {
  Date: {now: () => now, parse: Date.parse},
  setInterval: fn => { tick = fn; },
  document: {querySelectorAll: selector => ({
    ".countdown[data-deadline]": "data-deadline" in countdown.attrs ? [countdown] : [],
    "[data-before-deadline]": [cancel],
    "[data-after-deadline]": [phone],
  }[selector] || [])},
};
vm.runInNewContext(source, context);
assert.equal(countdown.textContent, "30 min left");
assert.equal(cancel.hidden, false);
assert.equal(phone.hidden, true);

now = Date.parse("2026-11-19T17:31:00Z");
tick();
assert.equal(countdown.textContent, "Pick-up time has passed");
assert.equal(cancel.hidden, true, "LP-27: Cancel is hidden after pick-up-by");
assert.equal(phone.hidden, false, "LP-27: the shop's phone is shown after pick-up-by");
console.log("countdown: minutes left, deadline passed, Cancel swapped for the shop's phone passed");
