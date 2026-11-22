/* LP-54: "Blurred with magic". A signed-in customer taps once on the blurred
 * photo; the server saves the spot and sends back a small circle of the real
 * photo, which is laid over the blurred one at that spot. One tap per offer:
 * a reload shows the same circle (the server puts it in the page).
 *
 * Photos are shown with object-fit: cover, so part of the picture can be
 * cropped. Tap points and circles are worked out on the whole picture, as
 * percentages of its width and height, so they match the server's.
 */
(function () {
  "use strict";

  // Where the whole picture is drawn inside the <img> box, in pixels.
  function drawnBox(img) {
    var boxWidth = img.clientWidth;
    var boxHeight = img.clientHeight;
    var width = img.naturalWidth || boxWidth;
    var height = img.naturalHeight || boxHeight;
    var fit = window.getComputedStyle(img).objectFit;
    var scale;
    if (fit === "cover") scale = Math.max(boxWidth / width, boxHeight / height);
    else if (fit === "contain") scale = Math.min(boxWidth / width, boxHeight / height);
    else return { left: 0, top: 0, width: boxWidth, height: boxHeight };
    return {
      left: (boxWidth - width * scale) / 2,
      top: (boxHeight - height * scale) / 2,
      width: width * scale,
      height: height * scale,
    };
  }

  function place(peek) {
    var img = peek.querySelector(".peek__photo");
    var circle = peek.querySelector(".peek__circle");
    if (!img || !circle || !peek.dataset.x) return;
    var box = drawnBox(img);
    var size = (Number(peek.dataset.size) / 100) * box.width;
    circle.style.width = size + "px";
    circle.style.left = img.offsetLeft + box.left + (Number(peek.dataset.x) / 100) * box.width + "px";
    circle.style.top = img.offsetTop + box.top + (Number(peek.dataset.y) / 100) * box.height + "px";
  }

  function show(peek, saved) {
    peek.dataset.x = saved.x;
    peek.dataset.y = saved.y;
    peek.dataset.size = saved.size;
    var circle = peek.querySelector(".peek__circle");
    if (!circle) {
      circle = document.createElement("img");
      circle.className = "peek__circle";
      circle.alt = "Your peek at the real photo";
      peek.appendChild(circle);
    }
    circle.src = saved.image;
    done(peek, "You've had your peek ✨");
    place(peek);
  }

  function done(peek, text) {
    peek.classList.add("peek--done");
    peek.removeAttribute("role");
    peek.removeAttribute("tabindex");
    peek.removeAttribute("aria-label");
    hint(peek, text);
  }

  function hint(peek, text) {
    var el = peek.querySelector("[data-peek-hint]");
    if (el) el.textContent = text;
  }

  function clamp(value) {
    return Math.min(100, Math.max(0, Math.round(value * 100) / 100));
  }

  function tap(peek, clientX, clientY) {
    if (peek.classList.contains("peek--done") || peek.dataset.busy) return;
    if (peek.dataset.viewer === "guest") {
      window.location.href = peek.dataset.loginUrl;
      return;
    }
    if (peek.dataset.viewer !== "customer") return;

    var img = peek.querySelector(".peek__photo");
    var rect = img.getBoundingClientRect();
    var box = drawnBox(img);
    // A keyboard press peeks at the middle of the photo.
    var x = clientX === null ? 50 : clamp(((clientX - rect.left - box.left) / box.width) * 100);
    var y = clientY === null ? 50 : clamp(((clientY - rect.top - box.top) / box.height) * 100);

    peek.dataset.busy = "1";
    hint(peek, "Peeking…");
    fetch(peek.dataset.peekUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": peek.dataset.csrf,
        "X-Peek-Page": window.location.pathname + window.location.search,
      },
      body: JSON.stringify({ x: x, y: y }),
    })
      .then(function (response) {
        return response.json().then(function (body) { return { status: response.status, body: body }; });
      })
      .then(function (result) {
        delete peek.dataset.busy;
        if (result.body.peek) {
          // A new peek (201), or the one already saved (409): the same circle.
          show(peek, result.body.peek);
        } else if (result.status === 401 && result.body.login_url) {
          window.location.href = result.body.login_url;
        } else if (result.status === 503 || result.status === 502) {
          hint(peek, "Couldn't peek right now. Tap to try again ✨");
        } else {
          done(peek, result.body.detail || "This photo can't be peeked at.");
        }
      })
      .catch(function () {
        delete peek.dataset.busy;
        hint(peek, "Couldn't peek right now. Tap to try again ✨");
      });
  }

  function setUp(peek) {
    var img = peek.querySelector(".peek__photo");
    if (img && !img.complete) img.addEventListener("load", function () { place(peek); });
    place(peek);

    peek.addEventListener("click", function (event) {
      // The login link in the hint works as a plain link.
      if (event.target.closest("a")) return;
      event.preventDefault();
      tap(peek, event.clientX, event.clientY);
    });
    peek.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      tap(peek, null, null);
    });
  }

  var peeks = document.querySelectorAll("[data-peek]");
  peeks.forEach(setUp);
  window.addEventListener("resize", function () { peeks.forEach(place); });
})();
