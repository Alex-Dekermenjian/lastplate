# LP-54: photo effect (Normal / Blurred / Blurred with magic), the blurred
# copy customers get, and one magic peek per customer per offer.

import django.db.models.deletion
import marketplace.upload_paths
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("marketplace", "0015_listing_max_per_customer"),
    ]

    operations = [
        migrations.CreateModel(
            name="PhotoPeek",
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
                ("x", models.DecimalField(decimal_places=2, max_digits=5)),
                ("y", models.DecimalField(decimal_places=2, max_digits=5)),
                ("size", models.DecimalField(decimal_places=2, max_digits=5)),
                ("cutout", models.BinaryField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddField(
            model_name="listing",
            name="photo_blurred",
            field=models.ImageField(
                blank=True,
                db_default="",
                default="",
                editable=False,
                upload_to=marketplace.upload_paths.listing_blurred_photo_upload_path,
            ),
        ),
        migrations.AddField(
            model_name="listing",
            name="photo_effect",
            field=models.CharField(
                choices=[
                    ("NORMAL", "Normal"),
                    ("BLURRED", "Blurred"),
                    ("MAGIC", "Blurred with magic"),
                ],
                db_default="NORMAL",
                default="NORMAL",
                max_length=10,
            ),
        ),
        migrations.AddConstraint(
            model_name="listing",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("photo_effect__in", ["NORMAL", "BLURRED", "MAGIC"])
                ),
                name="marketplace_listing_valid_photo_effect",
            ),
        ),
        migrations.AddField(
            model_name="photopeek",
            name="listing",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="photo_peeks",
                to="marketplace.listing",
            ),
        ),
        migrations.AddConstraint(
            model_name="photopeek",
            constraint=models.UniqueConstraint(
                fields=("listing", "customer_user_id"),
                name="marketplace_one_peek_per_customer_per_offer",
            ),
        ),
        migrations.AddConstraint(
            model_name="photopeek",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("x__gte", 0), ("x__lte", 100), ("y__gte", 0), ("y__lte", 100)
                ),
                name="marketplace_photopeek_point_inside_photo",
            ),
        ),
    ]
