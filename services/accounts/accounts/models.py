from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")

        email = self.normalize_email(email.strip()).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)

    def get_by_natural_key(self, username):
        return self.get(email__iexact=username.strip())


class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        SHOP_OPERATOR = "SHOP_OPERATOR", "Shop operator"
        # LP-57: the LastPlate administrator. Created only with the
        # create_admin management command, never by public sign-up.
        ADMIN = "ADMIN", "Admin"

    username = None
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=150)
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.CUSTOMER,
    )
    # LP-50: set when an admin gives the account a temporary password. The
    # person must choose a new password before doing anything else.
    must_change_password = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["display_name"]

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_ci_unique",
            ),
        ]

    def __str__(self):
        return self.email


class TemporaryPasswordLog(models.Model):
    """LP-50: who gave whom a temporary password, and when.

    The password itself is never stored here or anywhere else in plain text.
    Emails are copied so the record still reads correctly if an account is
    later deleted.
    """

    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    user_email = models.EmailField()
    set_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    set_by_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.set_by_email} set a temporary password for {self.user_email}"
