from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reservations", "0003_order_pickup_place")]
    operations = [migrations.AddField(
        model_name="order", name="customer_first_name",
        field=models.CharField(max_length=150, blank=True, default=""),
    )]
