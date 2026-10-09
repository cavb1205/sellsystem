from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Ventas', '0012_declaracionperdidaventa_venta_tipo_origen')]
    operations = [migrations.AddField(model_name='venta', name='calendario_version',
        field=models.PositiveSmallIntegerField(default=0, editable=False))]
