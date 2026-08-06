from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Tiendas', '0020_alertaoperativa'),
    ]

    operations = [
        migrations.AlterField(
            model_name='alertaoperativa',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('VENTA_CON_ALERTAS', 'Venta con señales operativas'),
                    ('RIESGO_CARTERA', 'Riesgo de cartera'),
                    ('SIN_PRIMER_ABONO', 'Crédito sin primer abono'),
                    ('CIERRE_AUSENTE', 'Cierre de caja ausente'),
                    ('RESUMEN_OPERATIVO', 'Resumen operativo'),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
