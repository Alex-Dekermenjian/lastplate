# LP-82: a customer's favourite restaurants (one row per customer and shop).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("marketplace", "0015_listing_max_per_customer"),
    ]

    operations = [
        migrations.CreateModel(
            name="Favourite",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("customer_user_id", models.PositiveBigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "shop",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="favourites",
                        to="marketplace.shop",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("customer_user_id", "shop"),
                        name="marketplace_favourite_unique_customer_shop",
                    )
                ],
            },
        ),
    ]
