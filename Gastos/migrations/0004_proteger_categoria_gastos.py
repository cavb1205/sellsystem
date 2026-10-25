from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('Gastos', '0003_alter_gasto_valor')]

    operations = [
        migrations.AlterField(
            model_name='gasto',
            name='tipo_gasto',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    to='Gastos.tipo_gasto'),
        ),
    ]
