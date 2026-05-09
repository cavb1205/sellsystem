from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Clientes', '0003_alter_cliente_estado_cliente'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cliente',
            name='identificacion',
            field=models.CharField(max_length=12),
        ),
        migrations.AddConstraint(
            model_name='cliente',
            constraint=models.UniqueConstraint(
                fields=['identificacion', 'tienda'],
                name='unique_cliente_identificacion_por_tienda'
            ),
        ),
    ]
