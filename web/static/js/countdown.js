/* LP-78: "58 min left" until an order's pick-up-by time.
 *
 * The server already writes the text when the page loads, so the page is
 * right without JavaScript; this only keeps it up to date. Once the time has
 * passed it says "Pick-up time has passed".
 */
(function () {
  "use strict";

  function text(minutes) {
    if (minutes < 0) return "Pick-up time has passed";
    if (minutes < 60) return minutes + " min left";
    return Math.floor(minutes / 60) + " h " + (minutes % 60) + " min left";
  }

  // LP-27: on a page left open past pick-up-by, swap Cancel for the
  // restaurant's phone (the server refuses a late cancel anyway).
  function afterDeadline() {
    document.querySelectorAll("[data-before-deadline]").forEach(function (el) { el.hidden = true; });
    document.querySelectorAll("[data-after-deadline]").forEach(function (el) { el.hidden = false; });
  }

  // The demo clock moves the whole app's time; the browser's own clock does not move.
  var clock = document.querySelector("[data-demo-clock]");
  var offset = clock ? Number(clock.getAttribute("data-offset-ms")) || 0 : 0;

  function update() {
    var now = Date.now() + offset;
    var passed = false;
    document.querySelectorAll(".countdown[data-deadline]").forEach(function (el) {
      var deadline = Date.parse(el.getAttribute("data-deadline"));
      if (isNaN(deadline)) return;
      var seconds = (deadline - now) / 1000;
      var minutes = seconds > 0 ? Math.floor(seconds / 60) : -1;
      el.textContent = text(minutes);
      if (minutes < 0) {
        el.classList.add("countdown--passed");
        el.removeAttribute("data-deadline");
        passed = true;
      }
    });
    if (passed) afterDeadline();
  }

  update();
  setInterval(update, 15000);
})();
