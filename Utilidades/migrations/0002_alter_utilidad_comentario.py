# Generated by Django 4.1 on 2022-11-16 17:13

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Utilidades', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='utilidad',
            name='comentario',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
    ]
