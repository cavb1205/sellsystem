# Generated by Django 4.1 on 2022-10-07 13:43

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Ventas', '0003_venta_fecha_vencimiento_venta_saldo_actual'),
    ]

    operations = [
        migrations.AlterField(
            model_name='venta',
            name='fecha_vencimiento',
            field=models.DateField(blank=True, null=True),
        ),
    ]
