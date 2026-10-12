from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('Tiendas', '0028_root_estados_configuracion'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='ReporteRutasTelegram', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('fecha', models.DateField()), ('chat_ref', models.CharField(max_length=64)),
            ('corte', models.DateTimeField(default=django.utils.timezone.now)),
            ('rutas', models.JSONField(default=list)), ('partes', models.JSONField(default=list)),
            ('actualizada', models.DateTimeField(auto_now=True)),
            ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.AddConstraint(model_name='reporterutastelegram', constraint=models.UniqueConstraint(
            fields=('usuario', 'fecha'), name='unique_reporte_rutas_usuario_fecha')),
    ]
