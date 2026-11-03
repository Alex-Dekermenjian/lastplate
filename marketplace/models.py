
from django.db import models


class Listing(models.Model):

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    shop = models.ForeignKey(
        "accounts.Shop",
        on_delete=models.PROTECT,
        related_name="listings",
    )


    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)


    original_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    discounted_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    total_quantity = models.PositiveIntegerField(default=0)


    pickup_address = models.CharField(
        max_length=255,
        blank=True,
    )

    pickup_start = models.DateTimeField(
        null=True,
        blank=True,
    )

    pickup_end = models.DateTimeField(
        null=True,
        blank=True,
    )


    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    terms_revision = models.PositiveIntegerField(default=1)

    first_reserved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title
