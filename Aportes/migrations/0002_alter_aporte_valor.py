# Generated by Django 4.1 on 2022-11-12 19:33

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Aportes', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='aporte',
            name='valor',
            field=models.DecimalField(decimal_places=0, max_digits=10),
        ),
    ]
