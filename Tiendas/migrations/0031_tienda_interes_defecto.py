# Escrita a mano: el estado de migraciones de Tiendas tiene drift y
# makemigrations propone operaciones ajenas a este cambio.
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('Tiendas', '0030_cuenta_transferencia_texto'),
    ]

    operations = [
        migrations.AddField(
            model_name='tienda',
            name='interes_defecto',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True,
                validators=[django.core.validators.MaxValueValidator(100)]),
        ),
        migrations.AddField(
            model_name='tienda',
            name='interes_aceptado_por',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='tienda',
            name='interes_aceptado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
