"""LP-68: a restaurant's branches, and the rules every page follows.

- A restaurant has 1 to 3 active branches. Branch 1 (the lowest position)
  is its main address, kept equal to Shop.address and Shop.area.
- Each branch is like its own store: its offers, stock and pickup address.
- Editing a branch's address updates its drafts; offers already live keep
  the address customers saw.
- A branch can be removed only when it has no drafts and no live offers,
  and never when it is the last one. Removing hides it (removed_at), so
  ended offers keep their branch.
"""

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from .models import MAX_BRANCHES, Branch, Listing, Shop

TOO_MANY = f"A restaurant can have at most {MAX_BRANCHES} branches."
NAME_TAKEN = "You already have a branch with this name."
LAST_BRANCH = "Your restaurant needs at least one branch."
HAS_DRAFTS = "This branch has draft offers. Move them to another branch or delete them first."
HAS_LIVE_OFFERS = "This branch has offers customers can still order. Remove it after they end."


class BranchError(Exception):
    """A branch rule was broken. errors maps a field (or "detail") to a message."""

    def __init__(self, errors):
        super().__init__(errors)
        self.errors = errors


def active_branches(shop):
    return Branch.objects.filter(shop=shop, removed_at__isnull=True).order_by("position", "id")


def main_branch(shop):
    return active_branches(shop).first()


def default_name(area):
    return (area or "").strip()[:100] or "Main branch"


def live_offers(branch, now=None):
    now = now or timezone.now()
    return branch.listings.filter(status=Listing.Status.PUBLISHED, pickup_end__gt=now)


def _lock(shop):
    """Serialise branch changes for one restaurant (the limit and positions)."""
    return Shop.objects.select_for_update().get(pk=shop.pk)


def _check_name(shop, name, exclude=None):
    taken = active_branches(shop).filter(name__iexact=name.strip())
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    if taken.exists():
        raise BranchError({"name": NAME_TAKEN})


def _update_drafts(branch):
    Listing.objects.filter(branch=branch, status=Listing.Status.DRAFT).update(
        pickup_address=branch.address, updated_at=timezone.now(),
    )


def _sync_shop_from_main(shop):
    """Shop.address/area always show branch 1 (profile, approval checks)."""
    main = main_branch(shop)
    if main is None:
        return
    if (shop.address, shop.area) != (main.address, main.area):
        shop.address, shop.area = main.address, main.area
        shop.save(update_fields=["address", "area", "updated_at"])


def ensure_first_branch(shop, name=None):
    """Give a restaurant without branches its branch 1 from its address."""
    with transaction.atomic():
        shop = _lock(shop)
        main = main_branch(shop)
        if main is None:
            main = Branch.objects.create(
                shop=shop, name=(name or "").strip()[:100] or default_name(shop.area),
                address=shop.address, area=shop.area, position=1,
            )
        return main


def sync_main_from_shop(shop):
    """The profile page changed the restaurant's address or area: branch 1
    follows, and so do its drafts."""
    main = ensure_first_branch(shop)
    if (main.address, main.area) != (shop.address, shop.area):
        main.address, main.area = shop.address, shop.area
        main.save(update_fields=["address", "area", "updated_at"])
        _update_drafts(main)
    return main


def add_branch(shop, name, address, area):
    with transaction.atomic():
        shop = _lock(shop)
        if active_branches(shop).count() >= MAX_BRANCHES:
            raise BranchError({"detail": TOO_MANY})
        _check_name(shop, name)
        position = (Branch.objects.filter(shop=shop).aggregate(top=Max("position"))["top"] or 0) + 1
        branch = Branch.objects.create(
            shop=shop, name=name.strip(), address=address.strip(), area=area.strip(), position=position,
        )
        _sync_shop_from_main(shop)
        return branch


def update_branch(branch, **changes):
    with transaction.atomic():
        shop = _lock(branch.shop)
        branch = Branch.objects.select_for_update().get(pk=branch.pk)
        if "name" in changes:
            _check_name(shop, changes["name"], exclude=branch)
        for field in ("name", "address", "area"):
            if field in changes:
                setattr(branch, field, changes[field].strip())
        branch.save()
        if "address" in changes:
            _update_drafts(branch)
        _sync_shop_from_main(shop)
        return branch


def remove_branch(branch):
    with transaction.atomic():
        shop = _lock(branch.shop)
        branch = Branch.objects.select_for_update().get(pk=branch.pk)
        if active_branches(shop).count() <= 1:
            raise BranchError({"detail": LAST_BRANCH})
        if branch.listings.filter(status=Listing.Status.DRAFT).exists():
            raise BranchError({"detail": HAS_DRAFTS})
        if live_offers(branch).exists():
            raise BranchError({"detail": HAS_LIVE_OFFERS})
        branch.removed_at = timezone.now()
        branch.save(update_fields=["removed_at", "updated_at"])
        # If branch 1 went, the next branch becomes the main address.
        _sync_shop_from_main(shop)


def set_application_branches(shop, first_name, others):
    """The restaurant's request (LP-37 step 2): branch 1 from its address,
    plus up to two more. others: [{"name", "address", "area"}].

    Branches are matched by their place in the list, so sending the request
    again updates them in place (drafts keep their branch); branches no
    longer sent are removed when that is allowed.
    """
    wanted = [{"name": first_name or default_name(shop.area), "address": shop.address, "area": shop.area}, *others]
    if len(wanted) > MAX_BRANCHES:
        raise BranchError({"other_branches": TOO_MANY})
    names = [entry["name"].strip().lower() for entry in wanted]
    if len(set(names)) != len(names):
        raise BranchError({"other_branches": NAME_TAKEN})

    with transaction.atomic():
        shop = _lock(shop)
        current = list(active_branches(shop))
        for index, entry in enumerate(wanted):
            values = {key: entry[key].strip() for key in ("name", "address", "area")}
            if index < len(current):
                branch = current[index]
                for field, value in values.items():
                    setattr(branch, field, value)
                branch.position = index + 1
                branch.save()
                _update_drafts(branch)
            else:
                Branch.objects.create(shop=shop, position=index + 1, **values)
        for branch in current[len(wanted):]:
            if not branch.listings.filter(Q(status=Listing.Status.DRAFT) | Q(status=Listing.Status.PUBLISHED)).exists():
                branch.removed_at = timezone.now()
                branch.save(update_fields=["removed_at", "updated_at"])
        _sync_shop_from_main(shop)
