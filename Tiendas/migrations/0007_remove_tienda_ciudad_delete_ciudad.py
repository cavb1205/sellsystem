# Generated by Django 4.1 on 2022-12-04 13:35

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0006_remove_tienda_moneda_alter_cierre_caja_valor_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='tienda',
            name='ciudad',
        ),
        migrations.DeleteModel(
            name='Ciudad',
        ),
    ]
