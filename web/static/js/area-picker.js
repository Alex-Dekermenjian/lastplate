/* LP-65: turn each <select class="area-picker"> into a search box with a list.
 *
 * Type "ham" and Hamra shows; other spellings work too ("ashrafieh" finds
 * Achrafieh). Pick with a click or tap, or with the arrow keys and Enter.
 * The original <select> stays in the form (hidden) and is what gets sent,
 * so the server only ever receives a place from the list. Without
 * JavaScript, the <select> is simply shown as a normal dropdown.
 */
(function () {
  "use strict";

  function normalize(text) {
    return (text || "")
      .normalize("NFD").replace(/[̀-ͯ]/g, "")
      .toLowerCase().replace(/['’-]/g, " ")
      .replace(/\s+/g, " ").trim();
  }

  function matches(query, searchText) {
    if (!query) return true;
    return searchText.split(" | ").some(function (text) {
      return text.indexOf(query) === 0 || (" " + text).indexOf(" " + query) !== -1;
    });
  }

  var counter = 0;

  function enhance(select) {
    counter += 1;
    var listId = "area-list-" + counter;
    var inputId = select.id;
    var selectedLabel = function () {
      var option = select.options[select.selectedIndex];
      return option && option.value ? option.textContent : "";
    };

    // The visible search box takes over the select's id, so its <label> and
    // help text point at what the person actually types in.
    select.id = inputId + "-select";
    select.classList.add("area-combo__select");
    select.setAttribute("tabindex", "-1");
    select.setAttribute("aria-hidden", "true");

    var wrapper = document.createElement("div");
    wrapper.className = "area-combo";
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);

    var input = document.createElement("input");
    input.type = "text";
    input.id = inputId;
    input.className = "area-combo__input";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    input.placeholder = select.dataset.placeholder || "Search";
    if (select.getAttribute("aria-describedby")) {
      input.setAttribute("aria-describedby", select.getAttribute("aria-describedby"));
    }
    if (select.getAttribute("aria-invalid")) {
      input.setAttribute("aria-invalid", select.getAttribute("aria-invalid"));
    }
    input.value = selectedLabel();
    wrapper.insertBefore(input, select);

    var list = document.createElement("ul");
    list.id = listId;
    list.className = "area-combo__list";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    wrapper.appendChild(list);

    // Every place once, with its region, in the select's order.
    var places = [];
    Array.prototype.forEach.call(select.querySelectorAll("option"), function (option) {
      if (!option.value) return;
      var group = option.parentNode.tagName === "OPTGROUP" ? option.parentNode.label : "";
      places.push({
        value: option.value,
        label: option.textContent,
        group: group,
        search: option.dataset.search || normalize(option.textContent),
      });
    });

    var shown = [];
    var active = -1;

    function render(query) {
      var normalized = normalize(query);
      shown = places.filter(function (place) { return matches(normalized, place.search); });
      list.innerHTML = "";
      active = -1;
      var lastGroup = null;
      shown.forEach(function (place, index) {
        if (place.group !== lastGroup) {
          lastGroup = place.group;
          var heading = document.createElement("li");
          heading.className = "area-combo__group";
          heading.setAttribute("role", "presentation");
          heading.textContent = place.group;
          list.appendChild(heading);
        }
        var item = document.createElement("li");
        item.id = listId + "-" + index;
        item.className = "area-combo__option";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", place.value === select.value ? "true" : "false");
        item.textContent = place.label;
        item.addEventListener("mousedown", function (event) {
          event.preventDefault(); // keep focus in the box
          choose(index);
        });
        list.appendChild(item);
      });
      if (!shown.length) {
        var empty = document.createElement("li");
        empty.className = "area-combo__empty";
        empty.setAttribute("role", "presentation");
        empty.textContent = "No place found. Try another spelling.";
        list.appendChild(empty);
      }
      if (normalized && shown.length) setActive(0);
    }

    function open() {
      if (!list.hidden) return;
      // Opening shows the whole list; typing narrows it.
      render(input.value === selectedLabel() ? "" : input.value);
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }

    function setActive(index) {
      var options = list.querySelectorAll('[role="option"]');
      if (!options.length) return;
      if (active >= 0 && options[active]) options[active].classList.remove("is-active");
      active = (index + options.length) % options.length;
      options[active].classList.add("is-active");
      input.setAttribute("aria-activedescendant", options[active].id);
      options[active].scrollIntoView({ block: "nearest" });
    }

    function choose(index) {
      var place = shown[index];
      if (!place) return;
      select.value = place.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      input.value = place.label;
      input.removeAttribute("aria-invalid");
      close();
    }

    input.addEventListener("focus", open);
    input.addEventListener("click", open);
    input.addEventListener("input", function () {
      if (list.hidden) { list.hidden = false; input.setAttribute("aria-expanded", "true"); }
      render(input.value);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (list.hidden) open();
        setActive(active + 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        if (list.hidden) open();
        setActive(active - 1);
      } else if (event.key === "Enter") {
        if (!list.hidden && active >= 0) {
          event.preventDefault(); // pick the place, don't send the form yet
          choose(active);
        }
      } else if (event.key === "Escape") {
        if (!list.hidden) {
          event.preventDefault();
          input.value = selectedLabel();
          close();
        }
      }
    });
    input.addEventListener("blur", function () {
      // Only a place from the list counts: an exact name is accepted,
      // anything else goes back to the last place picked (or empty).
      var typed = normalize(input.value);
      if (!typed) {
        select.value = "";
      } else {
        var exact = places.filter(function (place) { return normalize(place.label) === typed; })[0];
        if (exact) select.value = exact.value;
      }
      input.value = selectedLabel();
      close();
    });
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll("select.area-picker"), enhance);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
