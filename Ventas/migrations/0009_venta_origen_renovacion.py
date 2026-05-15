from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('Ventas', '0008_alter_venta_estado_venta'),
    ]

    operations = [
        migrations.AddField(
            model_name='venta',
            name='origen_renovacion',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='renovacion',
                to='Ventas.venta',
            ),
        ),
    ]
