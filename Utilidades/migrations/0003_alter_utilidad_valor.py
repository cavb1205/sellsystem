# Generated by Django 4.1 on 2022-11-16 17:18

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Utilidades', '0002_alter_utilidad_comentario'),
    ]

    operations = [
        migrations.AlterField(
            model_name='utilidad',
            name='valor',
            field=models.DecimalField(decimal_places=0, max_digits=10),
        ),
    ]
