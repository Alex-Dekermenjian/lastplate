# Joins LP-37 (0007_shop_application) and LP-57 (0007_shop_reviewed_by_user_id),
# which were written in parallel on top of 0006_stockadjustment. Neither is
# renamed, so a database that already applied either one keeps working.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("marketplace", "0007_shop_application"),
        ("marketplace", "0007_shop_reviewed_by_user_id"),
    ]

    operations = []
