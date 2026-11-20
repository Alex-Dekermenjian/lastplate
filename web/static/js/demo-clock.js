/* Demo clock: keeps the date and time in the top bar ticking.
 *
 * The server writes the current (demo) time when the page loads; this only
 * keeps it moving, in Lebanon time. Without JavaScript the page still shows
 * the time it was loaded.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-demo-clock]");
  if (!root) return;
  var text = root.querySelector("[data-demo-clock-text]");
  var start = Number(root.getAttribute("data-now-ms"));
  if (!text || isNaN(start)) return;

  var began = performance.now();
  var format = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Beirut", weekday: "short", day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
  });

  function show() {
    var parts = {};
    format.formatToParts(new Date(start + (performance.now() - began))).forEach(function (part) {
      parts[part.type] = part.value;
    });
    text.textContent = parts.weekday + " " + parts.day + " " + parts.month + " " + parts.year +
      " \u00b7 " + parts.hour + ":" + parts.minute + ":" + parts.second;
  }

  show();
  setInterval(show, 1000);
})();
