# Migración escrita a mano: SOLO crea PagoMembresia.
# (makemigrations local arrastraba drift preexistente —alters de id y
# Tienda_Administrador— que no debe tocarse en producción.)

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('Tiendas', '0015_cuentadestino'),
    ]

    operations = [
        migrations.CreateModel(
            name='PagoMembresia',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('monto', models.DecimalField(decimal_places=0, max_digits=10)),
                ('fecha', models.DateField(db_index=True)),
                ('origen', models.CharField(choices=[('telegram', 'Confirmado vía Telegram'), ('panel', 'Confirmado en panel web'), ('manual', 'Activación manual (root)')], default='telegram', max_length=20)),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('membresia', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='Tiendas.membresia')),
                ('registrado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('solicitud', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pagos', to='Tiendas.solicitudpago')),
                ('tienda', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pagos_membresia', to='Tiendas.tienda')),
            ],
        ),
    ]
