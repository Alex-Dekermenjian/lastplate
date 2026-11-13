/* LP-42: same-origin session requests; JWTs remain on the server. */
(function () {
  "use strict";
  var badge = document.querySelector("[data-orders-badge]");
  if (!badge) return;
  var pending = false;
  async function refresh() {
    if (pending) return;
    pending = true;
    try {
      var response = await fetch(badge.dataset.url, {credentials: "same-origin", cache: "no-store"});
      if (!response.ok || response.redirected) throw new Error("Unavailable");
      var data = await response.json();
      if (!Number.isInteger(data.count) || data.count < 0) throw new Error("Invalid count");
      badge.textContent = String(data.count);
      badge.setAttribute("aria-label", data.count + " active reserved orders");
      badge.removeAttribute("title");
    } catch (error) {
      badge.textContent = "—";
      badge.setAttribute("aria-label", "Order count unavailable");
      badge.setAttribute("title", "Order count unavailable; retrying shortly");
    } finally {
      pending = false;
    }
  }
  refresh();
  setInterval(refresh, 20000);
})();
