from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0009_tienda_prefijo_telefono'),
    ]

    operations = [
        migrations.AddField(
            model_name='tienda',
            name='cupo_minimo_nuevo',
            field=models.DecimalField(decimal_places=2, default=100000, max_digits=12),
        ),
    ]
