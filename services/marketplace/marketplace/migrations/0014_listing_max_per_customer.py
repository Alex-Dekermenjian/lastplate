# LP-84: the most units of an offer one customer may reserve.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0013_admin_moderation'),
    ]

    operations = [
        migrations.AddField(
            model_name='listing',
            name='max_per_customer',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='listing',
            constraint=models.CheckConstraint(
                condition=models.Q(('max_per_customer__isnull', True), ('max_per_customer__gte', 1), _connector='OR'),
                name='marketplace_listing_max_per_customer_positive',
            ),
        ),
    ]
