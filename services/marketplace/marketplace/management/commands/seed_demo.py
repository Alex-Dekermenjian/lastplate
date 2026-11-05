"""LP-15: Load the fictional Sprint 1 demonstration data into Marketplace.

Run through scripts/seed_demo.py, which first creates the operator accounts
in Accounts and passes their IDs here. Safe to repeat: it resets only the demo
shops' own data, so pickup times are always in the future on demo day.

- Cedar Crumb Bakery and Sunrise Patisserie: Approved, with published offers.
- Zaatar Corner: Pending with a complete profile, ready to approve live.
- A Marketplace team-admin account for the shop review page.
"""

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from marketplace.models import Listing, Shop, StockAdjustment

ADMIN_USERNAME = "demo-admin"

# Real food photos from Wikimedia Commons; licences in demo_photos/CREDITS.md.
PHOTOS = Path(__file__).resolve().parents[2] / "demo_photos"

APPROVED = Shop.ApprovalStatus.APPROVED
PENDING = Shop.ApprovalStatus.PENDING
PUBLISHED = Listing.Status.PUBLISHED
DRAFT = Listing.Status.DRAFT

# Pickup windows. ("in", hours from now, length) or ("tomorrow", local start hour, length).
SOON = ("in", 1, 4)
LATER_TODAY = ("in", 3, 3)
TOMORROW_MORNING = ("tomorrow", 8, 3)

SHOPS = [
    {
        "email": "cedar@demo.lastplate.test",
        "name": "Cedar Crumb Bakery (demo)",
        "address": "Bliss Street 12, near the AUB Main Gate",
        "area": "Hamra",
        "public_contact": "+961 1 350 000",
        "approval_status": APPROVED,
        "listings": [
            {
                "title": "Butter croissants, box of 4",
                "category": "Pastries",
                "description": "This morning's croissants, still flaky. Reheat for 3 minutes for best results.",
                "ingredients": "Wheat flour, butter, milk, sugar, yeast, salt",
                "allergens": "Gluten, milk",
                "prices": ("8.00", "3.50"),
                "quantity": 6,
                "pickup": SOON,
                "status": PUBLISHED,
                "photo": "croissants.jpg",
            },
            {
                "title": "Zaatar manakish, pack of 3",
                "category": "Savoury bakes",
                "description": "Baked at noon with olive oil and fresh zaatar.",
                "ingredients": "Wheat flour, zaatar, sesame, sumac, olive oil, salt",
                "allergens": "Gluten, sesame",
                "prices": ("6.00", "2.50"),
                "quantity": 10,
                "pickup": SOON,
                "status": PUBLISHED,
                "photo": "manakish.jpg",
            },
            {
                "title": "Sourdough loaf",
                "category": "Bread",
                "description": "A whole country loaf from today's bake. Keeps well for two days.",
                "ingredients": "Wheat flour, water, salt, sourdough starter",
                "allergens": "Gluten",
                "prices": ("5.00", "2.00"),
                "quantity": 4,
                "pickup": TOMORROW_MORNING,
                "status": PUBLISHED,
                "photo": "sourdough.jpg",
            },
            {
                # Kept as a draft so the demo can publish it live (LP-10).
                "title": "Cinnamon rolls, box of 6",
                "category": "Pastries",
                "description": "Soft rolls with cinnamon sugar and a cream cheese glaze.",
                "ingredients": "Wheat flour, butter, sugar, cinnamon, cream cheese, eggs",
                "allergens": "Gluten, milk, eggs",
                "prices": ("12.00", "5.00"),
                "quantity": 5,
                "pickup": LATER_TODAY,
                "status": DRAFT,
                "photo": "cinnamon.jpg",
            },
        ],
    },
    {
        "email": "sunrise@demo.lastplate.test",
        "name": "Sunrise Patisserie (demo)",
        "address": "Armenia Street 40",
        "area": "Mar Mikhael",
        "public_contact": "+961 1 440 000",
        "approval_status": APPROVED,
        "listings": [
            {
                "title": "Mixed mini tarts, box of 6",
                "category": "Desserts",
                "description": "Lemon, chocolate and berry mini tarts left from today's counter.",
                "ingredients": "Wheat flour, butter, sugar, eggs, lemon, chocolate, berries",
                "allergens": "Gluten, milk, eggs",
                "prices": ("15.00", "6.00"),
                "quantity": 3,
                "pickup": LATER_TODAY,
                "status": PUBLISHED,
                "photo": "tarts.jpg",
            },
            {
                "title": "Knefeh slices, tray of 4",
                "category": "Desserts",
                "description": "Cheese knefeh with syrup on the side. Best eaten warm.",
                "ingredients": "Semolina, akkawi cheese, butter, sugar syrup, orange blossom water",
                "allergens": "Gluten, milk",
                "prices": ("10.00", "4.00"),
                "quantity": 8,
                "pickup": SOON,
                "status": PUBLISHED,
                "photo": "knafeh.jpg",
            },
        ],
    },
    {
        # Pending: shows that an unapproved shop cannot publish (LP-9/LP-10)
        # and that its food never appears to visitors (LP-13).
        "email": "newshop@demo.lastplate.test",
        "name": "Zaatar Corner (demo)",
        "address": "Sassine Square 7",
        "area": "Achrafieh",
        "public_contact": "+961 1 200 000",
        "approval_status": PENDING,
        "listings": [
            {
                "title": "Cheese and zaatar croissants, box of 4",
                "category": "Savoury bakes",
                "description": "Half cheese, half zaatar, baked this afternoon.",
                "ingredients": "Wheat flour, butter, akkawi cheese, zaatar, sesame",
                "allergens": "Gluten, milk, sesame",
                "prices": ("9.00", "4.00"),
                "quantity": 6,
                "pickup": LATER_TODAY,
                "status": DRAFT,
                "photo": "cheese_croissant.jpg",
            },
        ],
    },
]


def pickup_window(kind, hour, length):
    """Aware start and end times, always in the future (Lebanon time)."""
    now = timezone.localtime()
    if kind == "in":
        start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour)
    else:
        start = (now + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return start, start + timedelta(hours=length)


class Command(BaseCommand):
    help = "Create or reset the Sprint 1 demo shops, offers and team-admin account."

    def add_arguments(self, parser):
        parser.add_argument("--operators", required=True, help='JSON from Accounts: {"email": user_id}.')
        parser.add_argument("--password", required=True, help="Password for the demo-admin account.")

    def handle(self, *args, operators, password, **options):
        try:
            operator_ids = json.loads(operators)
        except json.JSONDecodeError as error:
            raise CommandError(f"--operators is not valid JSON: {error}")
        missing = [shop["email"] for shop in SHOPS if shop["email"] not in operator_ids]
        if missing:
            raise CommandError(f"No Accounts ID given for: {', '.join(missing)}")

        old_photos = []
        with transaction.atomic():
            admin = self.reset_admin(password)
            for spec in SHOPS:
                old_photos += self.seed_shop(spec, operator_ids[spec["email"]], admin)

        # Delete replaced photo files only once the new data is committed.
        for photo in old_photos:
            photo.storage.delete(photo.name)
        self.stdout.write(self.style.SUCCESS("Marketplace demo data is ready."))

    def reset_admin(self, password):
        User = get_user_model()
        admin, _ = User.objects.get_or_create(username=ADMIN_USERNAME)
        admin.is_staff = True
        admin.is_superuser = True
        admin.is_active = True
        admin.set_password(password)
        admin.save()
        return admin

    def seed_shop(self, spec, operator_user_id, admin):
        approved = spec["approval_status"] == APPROVED
        shop, _ = Shop.objects.update_or_create(
            operator_user_id=operator_user_id,
            defaults={
                "name": spec["name"],
                "address": spec["address"],
                "area": spec["area"],
                "public_contact": spec["public_contact"],
                "approval_status": spec["approval_status"],
                "rejection_reason": "",
                "reviewed_by": admin if approved else None,
                "reviewed_at": timezone.now() if approved else None,
            },
        )

        old_listings = Listing.objects.filter(shop_id=shop.pk)
        old_photos = [listing.photo for listing in old_listings if listing.photo]
        StockAdjustment.objects.filter(listing__in=old_listings).delete()
        old_listings.delete()

        for item in spec["listings"]:
            start, end = pickup_window(*item["pickup"])
            original, discounted = item["prices"]
            listing = Listing(
                shop_id=shop.pk,
                title=item["title"],
                description=item["description"],
                category=item["category"],
                ingredients=item["ingredients"],
                allergens=item["allergens"],
                original_price=Decimal(original),
                discounted_price=Decimal(discounted),
                total_quantity=item["quantity"],
                pickup_address=shop.address,
                pickup_start=start,
                pickup_end=end,
                status=item["status"],
            )
            with open(PHOTOS / item["photo"], "rb") as photo:
                listing.photo.save(item["photo"], File(photo), save=False)
            listing.full_clean()
            listing.save()
        return old_photos
