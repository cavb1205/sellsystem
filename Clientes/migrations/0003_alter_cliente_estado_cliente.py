# Generated by Django 4.0.3 on 2022-06-10 18:21

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Clientes', '0002_cliente_nombre_local_alter_cliente_estado_cliente_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cliente',
            name='estado_cliente',
            field=models.CharField(choices=[('Activo', 'Activo'), ('Inactivo', 'Inactivo'), ('Bloqueado', 'Bloqueado')], default='Activo', max_length=50),
        ),
    ]
