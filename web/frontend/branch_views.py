"""LP-68: the owner's Branches page: up to 3 branches, each like its own
store under the restaurant's name ("ZWZ · Hamra")."""

from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from . import services
from .account_views import authenticated_call, operator_required
from .apply_views import approved_shop_required
from .area_field import AreaField, area_choices
from .services import ServiceUnavailable

MAX_BRANCHES = 3
UNAVAILABLE = "We can't reach the shop service right now. Please try again in a few minutes."


class BranchForm(forms.Form):
    name = forms.CharField(
        label="Branch name", min_length=2, max_length=100,
        help_text="What customers see after your restaurant's name, for example Hamra.",
    )
    address = forms.CharField(
        label="Street address", min_length=5, max_length=255,
        help_text="Where customers pick up from this branch.",
        widget=forms.TextInput(attrs={"autocomplete": "street-address"}),
    )
    area = AreaField()

    def __init__(self, *args, current_area=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["area"].choices = area_choices(current_area)


def why_not_removable(branch, count):
    """Why a branch can't be removed now, or "" if it can."""
    if count <= 1:
        return "Your restaurant needs at least one branch."
    if branch.get("draft_offers"):
        return "It has draft offers. Move them to another branch first."
    if branch.get("live_offers"):
        return "It has offers customers can still order. You can remove it after they end."
    return ""


def load_branches(request):
    try:
        status, data = authenticated_call(request, services.my_branches)
    except ServiceUnavailable:
        return None
    return data if status == 200 and isinstance(data, list) else None


def page(request, branches, add_form=None, edit_forms=None, status=200):
    edit_forms = edit_forms or {}
    rows = [
        {
            **branch,
            "form": edit_forms.get(branch["id"]) or BranchForm(
                prefix=f"branch-{branch['id']}",
                initial={key: branch.get(key, "") for key in ("name", "address", "area")},
                current_area=branch.get("area"),
            ),
            "open": branch["id"] in edit_forms,
            "blocked": why_not_removable(branch, len(branches)),
        }
        for branch in branches
    ]
    can_add = len(branches) < MAX_BRANCHES
    return render(request, "frontend/branches.html", {
        "branches": rows,
        "can_add": can_add,
        "add_form": add_form or (BranchForm(prefix="new") if can_add else None),
        "add_open": add_form is not None,
        "max_branches": MAX_BRANCHES,
    }, status=status)


def show_marketplace_errors(form, body):
    for field, problems in (body or {}).items():
        problems = problems if isinstance(problems, list) else [str(problems)]
        form.add_error(field if field in form.fields else None, problems)


@operator_required
@require_http_methods(["GET", "POST"])
@approved_shop_required
def branches(request):
    current = load_branches(request)
    if current is None:
        messages.error(request, UNAVAILABLE)
        return redirect("workspace")

    if request.method != "POST":
        return page(request, current)

    form = BranchForm(request.POST, prefix="new")
    if not form.is_valid():
        return page(request, current, add_form=form, status=400)
    try:
        status, body = authenticated_call(request, services.add_branch, form.cleaned_data)
    except ServiceUnavailable:
        form.add_error(None, UNAVAILABLE)
        return page(request, current, add_form=form, status=503)
    if status != 201:
        show_marketplace_errors(form, body)
        return page(request, current, add_form=form, status=400)
    messages.success(request, f"Branch {form.cleaned_data['name']} added.")
    return redirect("branches")


@operator_required
@require_POST
@approved_shop_required
def edit_branch(request, branch_id):
    current = load_branches(request)
    if current is None:
        messages.error(request, UNAVAILABLE)
        return redirect("workspace")
    branch = next((item for item in current if item.get("id") == branch_id), None)
    if branch is None:
        messages.error(request, "That branch wasn't found.")
        return redirect("branches")

    form = BranchForm(request.POST, prefix=f"branch-{branch_id}", current_area=branch.get("area"))
    if not form.is_valid():
        return page(request, current, edit_forms={branch_id: form}, status=400)
    try:
        status, body = authenticated_call(request, services.update_branch, branch_id, form.cleaned_data)
    except ServiceUnavailable:
        form.add_error(None, UNAVAILABLE)
        return page(request, current, edit_forms={branch_id: form}, status=503)
    if status != 200:
        show_marketplace_errors(form, body)
        return page(request, current, edit_forms={branch_id: form}, status=400)
    messages.success(request, "Branch saved. Draft offers there use the new address; live offers keep theirs.")
    return redirect("branches")


@operator_required
@require_POST
@approved_shop_required
def remove_branch(request, branch_id):
    try:
        status, body = authenticated_call(request, services.remove_branch, branch_id)
    except ServiceUnavailable:
        messages.error(request, UNAVAILABLE)
        return redirect("branches")
    if status == 204:
        messages.success(request, "Branch removed.")
    elif status == 404:
        messages.error(request, "That branch wasn't found.")
    else:
        detail = body.get("detail") if isinstance(body, dict) else None
        messages.error(request, detail or "That branch can't be removed right now.")
    return redirect("branches")
