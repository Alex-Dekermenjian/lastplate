"""Create or reset the fictional demo shop-operator accounts.

Run through scripts/seed_demo.py, which passes the result on to Marketplace.
Prints one JSON object mapping each demo email to its Accounts user ID.
Every demo account uses an @demo.lastplate.test email, so it is easy to spot.
"""

import json

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User

DEMO_OPERATORS = {
    "cedar@demo.lastplate.test": "Cedar Crumb Bakery operator",
    "sunrise@demo.lastplate.test": "Sunrise Patisserie operator",
    "newshop@demo.lastplate.test": "Zaatar Corner operator",
}


# A customer account for the customer path. Not passed to Marketplace.
DEMO_CUSTOMERS = {"customer@demo.lastplate.test": "Maya (demo customer)"}


class Command(BaseCommand):
    help = "Create or reset the demo shop-operator accounts and print their IDs as JSON."

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True, help="Password given to every demo account.")

    @transaction.atomic
    def handle(self, *args, password, **options):
        ids = {}
        for email, display_name in DEMO_OPERATORS.items():
            user, _ = User.objects.get_or_create(
                email=email,
                defaults={"display_name": display_name, "role": User.Role.SHOP_OPERATOR},
            )
            user.display_name = display_name
            user.role = User.Role.SHOP_OPERATOR
            user.is_active = True
            user.set_password(password)
            user.save()
            ids[email] = user.pk
        for email, display_name in DEMO_CUSTOMERS.items():
            user, _ = User.objects.get_or_create(
                email=email, defaults={"display_name": display_name, "role": User.Role.CUSTOMER}
            )
            user.display_name = display_name
            user.role = User.Role.CUSTOMER
            user.is_active = True
            user.set_password(password)
            user.save()
        self.stdout.write(json.dumps(ids))
