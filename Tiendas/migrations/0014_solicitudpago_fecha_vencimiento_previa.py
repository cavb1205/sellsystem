from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0013_solicitudpago_telegram_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='solicitudpago',
            name='fecha_vencimiento_previa',
            field=models.DateField(blank=True, null=True),
        ),
    ]
