"""LP-57: create a LastPlate administrator (the Admin role).

This is the only way to create an Admin account; public sign-up never can.
The password is read from the LASTPLATE_ADMIN_PASSWORD environment variable
when it is set (for scripts), otherwise it is asked for twice without echo.
It is never accepted as a command-line argument, so it cannot end up in
shell history.

    python manage.py create_admin --email admin@example.test --name "LastPlate Admin"
"""

import getpass
import os

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

PASSWORD_ENV = "LASTPLATE_ADMIN_PASSWORD"


class Command(BaseCommand):
    help = "Create a LastPlate administrator account (Admin role)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True, help="Display name, for example 'LastPlate Admin'.")

    def handle(self, *args, **options):
        User = get_user_model()
        email = User.objects.normalize_email(options["email"].strip()).lower()
        name = options["name"].strip()

        try:
            validate_email(email)
        except ValidationError:
            raise CommandError("Enter a valid email address.")
        if not name:
            raise CommandError("Enter a display name.")
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError("An account with this email already exists.")

        password = self.read_password()
        try:
            password_validation.validate_password(password, user=User(email=email, display_name=name))
        except ValidationError as error:
            raise CommandError(" ".join(error.messages))

        try:
            with transaction.atomic():
                # Not is_staff or is_superuser: the Admin role works in /console,
                # not in the Accounts Django admin.
                User.objects.create_user(email, password, display_name=name, role=User.Role.ADMIN)
        except IntegrityError:
            raise CommandError("An account with this email already exists.")

        self.stdout.write(self.style.SUCCESS(f"Admin account created for {email}."))

    def read_password(self):
        password = os.environ.get(PASSWORD_ENV)
        if password:
            return password
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Password (again): "):
            raise CommandError("The two passwords didn't match.")
        return password
