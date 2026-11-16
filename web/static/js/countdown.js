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

  function update() {
    var now = Date.now();
    document.querySelectorAll(".countdown[data-deadline]").forEach(function (el) {
      var deadline = Date.parse(el.getAttribute("data-deadline"));
      if (isNaN(deadline)) return;
      var seconds = (deadline - now) / 1000;
      var minutes = seconds > 0 ? Math.floor(seconds / 60) : -1;
      el.textContent = text(minutes);
      if (minutes < 0) {
        el.classList.add("countdown--passed");
        el.removeAttribute("data-deadline");
      }
    });
  }

  update();
  setInterval(update, 15000);
})();
