import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reservations', '0007_customer_block'),
    ]

    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ref', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('customer_user_id', models.PositiveBigIntegerField(db_index=True)),
                ('kind', models.CharField(choices=[('TEN_MINUTES_LEFT', '10 minutes left'), ('SHOP_CANCELLED', 'Cancelled by the restaurant'), ('EXPIRED', 'Expired')], max_length=20)),
                ('message', models.TextField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='notifications', to='reservations.order')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'constraints': [models.UniqueConstraint(fields=('order', 'kind'), name='notification_one_per_order_and_kind')],
            },
        ),
    ]
