from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0023_movimientocaja'),
    ]

    operations = [
        migrations.AddField(
            model_name='tienda',
            name='zona_horaria',
            field=models.CharField(default='America/Santiago', max_length=64),
        ),
    ]
