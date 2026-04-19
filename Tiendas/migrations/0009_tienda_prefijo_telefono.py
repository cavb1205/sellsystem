from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0008_membresia_tienda_membresia'),
    ]

    operations = [
        migrations.AddField(
            model_name='tienda',
            name='prefijo_telefono',
            field=models.CharField(blank=True, default='56', max_length=5),
        ),
    ]
