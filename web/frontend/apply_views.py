"""LP-37: a restaurant applies to join LastPlate, then waits for review.

The request has three steps. Answers are kept in the server-side session, so
going back or fixing a mistake never loses them. Images chosen on the last
step are kept in a private folder until the request is sent.

Owners whose restaurant isn't approved see their request status and can't
open offer pages.
"""

import json
import os
import re
import time
from functools import wraps
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.shortcuts import redirect, render

from . import services
from .account_views import authenticated_call, operator_required, unapproved_shop_page
from .apply_forms import LocationForm, PhotosForm, RestaurantInfoForm, other_branches
from .services import ServiceUnavailable

APPLICATION_KEY = "restaurant_application"

STEPS = {
    1: ("Restaurant info", RestaurantInfoForm),
    2: ("Location", LocationForm),
    3: ("Photos and conditions", PhotosForm),
}
STEP_FIELDS = {number: list(form.base_fields) for number, (_title, form) in STEPS.items()}
# LP-68: Marketplace reports problems with the extra branches under this name.
STEP_FIELDS[2].append("other_branches")
IMAGE_FIELDS = ("photo", "logo")
HELD_NAME = re.compile(r"[0-9a-f]{32}\.(jpg|png)")
HELD_MAX_AGE = 24 * 60 * 60

SHOP_SERVICE_DOWN = "We can't reach the shop service right now. Please try again in a few minutes."
NOT_SENT = (
    "We couldn't reach LastPlate to send your request. Your answers are kept; "
    "please try again in a few minutes."
)


# ---------- The owner's shop ----------

def load_my_shop(request):
    """(status, shop) from Marketplace; status is None when it can't be reached."""
    try:
        status, shop = authenticated_call(request, services.my_shop)
    except ServiceUnavailable:
        return None, None
    return status, shop if isinstance(shop, dict) else None


def approved_shop_required(view):
    """Offer pages are only for owners whose restaurant is approved. Others see
    their request instead. Use inside operator_required."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        status, shop = load_my_shop(request)
        if status == 404:
            return redirect("restaurant-apply")
        if status != 200 or shop is None:
            messages.error(request, SHOP_SERVICE_DOWN)
            return redirect("workspace")
        page = unapproved_shop_page(shop)
        if page:
            return redirect(page)
        request.shop = shop
        return view(request, *args, **kwargs)

    return wrapper


# ---------- Images kept between tries ----------

def held_storage():
    return FileSystemStorage(location=settings.APPLICATION_UPLOAD_DIR)


def prune_old_held_files(storage):
    """Remove images from requests that were never sent."""
    try:
        _dirs, names = storage.listdir("")
    except FileNotFoundError:
        return
    cutoff = time.time() - HELD_MAX_AGE
    for name in names:
        try:
            if os.path.getmtime(storage.path(name)) < cutoff:
                storage.delete(name)
        except OSError:
            pass


def hold_image(state, field, uploaded):
    storage = held_storage()
    prune_old_held_files(storage)
    extension = "png" if uploaded.name.lower().endswith(".png") else "jpg"
    uploaded.seek(0)
    stored = storage.save(f"{uuid4().hex}.{extension}", uploaded)
    drop_held_image(state, field)
    state["held"][field] = {
        "stored": stored,
        "filename": os.path.basename(uploaded.name)[:100],
        "content_type": "image/png" if extension == "png" else "image/jpeg",
    }


def drop_held_image(state, field):
    info = state["held"].pop(field, None)
    if info and HELD_NAME.fullmatch(info.get("stored", "")):
        held_storage().delete(info["stored"])


def open_held_image(info):
    if not HELD_NAME.fullmatch(info.get("stored", "")):
        return None
    try:
        with held_storage().open(info["stored"], "rb") as held:
            content = held.read()
    except OSError:
        return None
    return SimpleUploadedFile(info["filename"], content, content_type=info["content_type"])


# ---------- Answers kept in the session ----------

def application_state(request, shop):
    state = request.session.get(APPLICATION_KEY)
    if state is None:
        # Start from what an earlier, name-only setup already saved.
        shop = shop or {}
        values = {
            field: shop.get(field) or ""
            for field in ("name", "public_contact", "address", "area")
        }
        state = {"values": values, "done": [], "held": {}}
    return state


def save_state(request, state):
    request.session[APPLICATION_KEY] = state
    request.session.modified = True


def remember_values(state, step, data):
    for field in STEP_FIELDS[step]:
        if field in IMAGE_FIELDS:
            continue
        if field == "accept_conditions":
            state["values"][field] = bool(data.get(field))
        else:
            value = data.get(field)
            state["values"][field] = "" if value is None else str(value).strip()


def mark(state, step, done):
    steps = set(state["done"])
    if done:
        steps.add(step)
    else:
        steps.discard(step)
    state["done"] = sorted(steps)


def first_unfinished_step(state):
    for number in STEPS:
        if number not in state["done"]:
            return number
    return len(STEPS)


def clear_application(request, state):
    for field in list(state["held"]):
        drop_held_image(state, field)
    request.session.pop(APPLICATION_KEY, None)


# ---------- Pages ----------

@operator_required
def restaurant_apply(request, step=1):
    if step not in STEPS:
        raise Http404

    status, shop = load_my_shop(request)
    if status == 200 and shop is not None:
        page = unapproved_shop_page(shop)
        if page != "restaurant-apply":
            return redirect(page or "workspace")

    state = application_state(request, shop if status == 200 else None)
    allowed = first_unfinished_step(state)
    if step > allowed:
        return redirect("restaurant-apply", step=allowed)

    title, form_class = STEPS[step]
    form_kwargs = {"held": state["held"]} if form_class is PhotosForm else {}
    if form_class is LocationForm:
        form_kwargs["current_area"] = state["values"].get("area")

    if request.method == "POST":
        form = form_class(request.POST, request.FILES, **form_kwargs)
        going_back = "back" in request.POST and step > 1
        valid = form.is_valid()

        if form_class is PhotosForm:
            # Keep every image that passed its check, whatever else is wrong.
            for field in IMAGE_FIELDS:
                uploaded = request.FILES.get(field)
                if uploaded and field not in form.errors:
                    hold_image(state, field, uploaded)

        remember_values(state, step, form.cleaned_data if valid else request.POST)
        mark(state, step, valid)
        save_state(request, state)

        if going_back:
            return redirect("restaurant-apply", step=step - 1)
        if valid and step < len(STEPS):
            return redirect("restaurant-apply", step=step + 1)
        if valid:
            sent = send_application(request, state, form)
            if sent:
                return sent
            save_state(request, state)
    else:
        form = form_class(initial=state["values"], **form_kwargs)

    return render(
        request,
        "frontend/restaurant_apply.html",
        {
            "form": form,
            "step": step,
            "step_title": title,
            "step_count": len(STEPS),
            "steps": list(STEPS),
            "held": state["held"],
            "is_last": step == len(STEPS),
        },
    )


def send_application(request, state, form):
    """Send every answer to Marketplace. Returns a redirect when finished,
    or None after adding errors to the form."""
    values = state["values"]
    fields = {
        field: values[field]
        for field in ("name", "category", "public_contact", "address", "area")
    }
    if values.get("latitude") and values.get("longitude"):
        fields["latitude"] = values["latitude"]
        fields["longitude"] = values["longitude"]
    # LP-68: the first branch's name and any other branches.
    if values.get("branch_name"):
        fields["branch_name"] = values["branch_name"]
    extra = other_branches(values)
    if extra:
        fields["other_branches"] = json.dumps(extra)
    fields["accept_conditions"] = "true"

    files = {}
    for field, info in list(state["held"].items()):
        image = open_held_image(info)
        if image is None:
            state["held"].pop(field)
            if field == "photo":
                form.add_error("photo", "Please choose your cover photo again.")
                return None
            continue
        files[field] = image

    try:
        status, body = authenticated_call(request, services.submit_application, fields, files)
    except ServiceUnavailable:
        form.add_error(None, NOT_SENT)
        return None

    if status in (200, 201):
        clear_application(request, state)
        messages.success(request, "Your request has been sent to the LastPlate team.")
        return redirect("request-status")
    if status == 409:
        clear_application(request, state)
        return redirect("workspace")
    if status == 400 and isinstance(body, dict):
        show_refusal(state, form, body)
        return None
    form.add_error(None, NOT_SENT)
    return None


def show_refusal(state, form, errors):
    """Marketplace refused some answers: show each on this page, and reopen
    the step it belongs to."""
    labels = {
        field: form_class.base_fields[field].label
        for _title, form_class in STEPS.values()
        for field in form_class.base_fields
    }
    for field, problems in errors.items():
        problems = problems if isinstance(problems, list) else [str(problems)]
        step = next((number for number, names in STEP_FIELDS.items() if field in names), None)
        if step == len(STEPS):
            if field in IMAGE_FIELDS:
                drop_held_image(state, field)
            form.add_error(field, problems)
            continue
        if step:
            mark(state, step, False)
        label = labels.get(field)
        for problem in problems:
            form.add_error(None, f"{label}: {problem}" if label else str(problem))


@operator_required
def request_status(request):
    """The owner's request: sent and waiting, or not approved (with the reason)."""
    status, shop = load_my_shop(request)
    if status == 404:
        return redirect("restaurant-apply")
    if status != 200 or shop is None:
        return render(request, "frontend/request_status.html", {"unavailable": True})

    page = unapproved_shop_page(shop)
    if page != "request-status":
        return redirect(page or "workspace")

    return render(
        request,
        "frontend/request_status.html",
        {"shop": shop, "rejected": shop.get("approval_status") == "REJECTED"},
    )
